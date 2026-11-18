package com.smallylc.smartyouth.data

import android.content.Context
import com.smallylc.smartyouth.app
import com.smallylc.smartyouth.site.Member
import com.smallylc.smartyouth.site.MemberParser
import com.smallylc.smartyouth.site.OrgNode
import com.smallylc.smartyouth.site.SiteClient
import com.smallylc.smartyouth.site.SiteMessage
import com.smallylc.smartyouth.site.toOverview
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 一个进程内唯一的站点数据源：当前账号、团员名单、站内信。
 *
 * 名单在本地留一份缓存：扫描保存时要在没有网络的场景下也能靠名字补齐文件名，
 * 所以名单不能只存在内存里。
 */
object Repo {

    private val store by lazy { AccountStore(app) }

    val accounts: AccountStore get() = store

    var client: SiteClient? = null
        private set

    private val _sessionState = MutableStateFlow(SessionState())
    val sessionState: StateFlow<SessionState> = _sessionState.asStateFlow()

    private val _members = MutableStateFlow<List<Member>>(emptyList())
    val members: StateFlow<List<Member>> = _members.asStateFlow()

    private val _orgs = MutableStateFlow<List<OrgNode>>(emptyList())
    val orgs: StateFlow<List<OrgNode>> = _orgs.asStateFlow()

    private val _messages = MutableStateFlow<List<SiteMessage>>(emptyList())
    val messages: StateFlow<List<SiteMessage>> = _messages.asStateFlow()

    private val _syncState = MutableStateFlow(SyncState())
    val syncState: StateFlow<SyncState> = _syncState.asStateFlow()

    val activeUserName: String get() = store.activeUserName

    fun clientFor(): SiteClient? {
        val name = store.activeUserName
        if (name.isBlank()) return null
        val existing = client
        if (existing != null && existing.userName == name) return existing
        return SiteClient(store, name).also { client = it }
    }

    /** 启动时恢复：有账号就先试会话，会话失效则标记未登录。 */
    suspend fun restore() = withContext(Dispatchers.IO) {
        val account = store.active
        if (account == null) {
            _sessionState.value = SessionState(loggedIn = false)
            return@withContext
        }
        loadCache(account.userName)
        val c = clientFor() ?: return@withContext
        val (ok, message) = c.sessionAccount()
        _sessionState.value = SessionState(loggedIn = ok, userName = account.userName, message = message)
        if (ok) refreshAll(quiet = true)
    }

    suspend fun login(userName: String, password: String, captcha: String = ""): LoginOutcome =
        withContext(Dispatchers.IO) {
            store.upsert(Account(userName = userName, password = password))
            client = SiteClient(store, userName)
            val verdict = client!!.login(captcha)
            when (verdict.kind) {
                "ok" -> {
                    _sessionState.value = SessionState(loggedIn = true, userName = userName, message = verdict.message)
                    refreshAll(quiet = true)
                    LoginOutcome.Success
                }
                "captcha" -> LoginOutcome.NeedCaptcha(verdict.message)
                else -> {
                    _sessionState.value = SessionState(loggedIn = false, userName = userName, message = verdict.message)
                    LoginOutcome.Failed(verdict.message)
                }
            }
        }

    suspend fun fetchCaptcha(): ByteArray? = withContext(Dispatchers.IO) { client?.fetchCaptcha() }

    fun logout() {
        client?.let { store.clearCookies(it.userName) }
        client = null
        _members.value = emptyList()
        _orgs.value = emptyList()
        _messages.value = emptyList()
        _sessionState.value = SessionState(loggedIn = false)
    }

    /** 名单 + 站内信 + 组织树一起拉；会员页单独刷新只更新名单与未读数。 */
    suspend fun refreshAll(quiet: Boolean = false) = withContext(Dispatchers.IO) {
        val c = clientFor() ?: return@withContext
        _syncState.value = SyncState(syncing = true, quiet = quiet)
        var error: String? = null
        try {
            val members = c.allMembers()
            _members.value = members
            runCatching { c.orgTree() }.getOrNull()?.let { _orgs.value = it }
            val messages = runCatching { c.messagesPage(1) }.getOrDefault(emptyList())
            _messages.value = messages
            saveCache(c.userName, members)
        } catch (e: Exception) {
            error = e.message
        }
        _syncState.value = SyncState(syncing = false, quiet = quiet, error = error, lastSync = System.currentTimeMillis())
        if (error == null) {
            _sessionState.value = _sessionState.value.copy(loggedIn = true)
        }
    }

    fun overview() = _members.value.toOverview(unread = _messages.value.count { it.unread })

    /** 保存扫描件时用的名单候选：按姓名/手机号模糊匹配，优先前缀命中。 */
    fun searchMembers(query: String, limit: Int = 8): List<Member> {
        val q = query.trim()
        val all = _members.value
        if (q.isEmpty()) return all.take(limit)
        return all.asSequence()
            .mapNotNull { member ->
                val score = score(member, q)
                if (score < 0) null else score to member
            }
            .sortedBy { it.first }
            .take(limit)
            .map { it.second }
            .toList()
    }

    private fun score(member: Member, q: String): Int {
        val name = member.name
        val mobile = member.mobile
        return when {
            name == q || mobile == q -> 0
            name.startsWith(q) || mobile.startsWith(q) -> 1
            name.contains(q) || mobile.contains(q) -> 2
            isSubsequence(q, name) -> 3
            else -> -1
        }
    }

    private fun isSubsequence(query: String, target: String): Boolean {
        var i = 0
        for (ch in target) {
            if (i < query.length && ch == query[i]) i++
        }
        return i == query.length
    }

    // ---------- 本地缓存 ----------

    private fun cacheFile(userName: String): File =
        File(app.filesDir, "roster/${userName.replace(Regex("[^\\w.-]"), "_")}.json")

    private suspend fun saveCache(userName: String, members: List<Member>) = withContext(Dispatchers.IO) {
        runCatching {
            val array = JSONArray()
            members.forEach { m ->
                array.put(
                    JSONObject()
                        .put("mid", m.mid)
                        .put("name", m.name)
                        .put("mobile", m.mobile)
                        .put("oid", m.oid)
                        .put("orgName", m.orgName)
                        .put("ebookStatus", m.ebookStatus)
                        .put("reportStatus", m.reportStatus)
                        .put("realNameStatus", m.realNameStatus)
                        .put("unpaidTotal", m.unpaidTotal)
                        .put("unpaidMonths", m.unpaidMonths)
                )
            }
            cacheFile(userName).apply { parentFile?.mkdirs() }.writeText(array.toString())
        }
    }

    private suspend fun loadCache(userName: String) = withContext(Dispatchers.IO) {
        if (_members.value.isNotEmpty()) return@withContext
        val file = cacheFile(userName)
        if (!file.exists()) return@withContext
        runCatching {
            val array = JSONArray(file.readText())
            _members.value = MemberParser.parseRows(array)
        }
    }
}

data class SessionState(
    val loggedIn: Boolean = false,
    val userName: String = "",
    val message: String = "",
)

data class SyncState(
    val syncing: Boolean = false,
    val quiet: Boolean = false,
    val error: String? = null,
    val lastSync: Long = 0,
)

sealed interface LoginOutcome {
    data object Success : LoginOutcome
    data class NeedCaptcha(val message: String) : LoginOutcome
    data class Failed(val message: String) : LoginOutcome
}
