package com.smallylc.smartyouth.data

import android.content.Context
import androidx.core.content.edit
import com.smallylc.smartyouth.app
import org.json.JSONArray
import org.json.JSONObject

/**
 * 站点账号。密码只落在本机私有目录，和桌面端一样是明文存储 ——
 * 换设备时靠用户自己保管，不做「看似加密」的处理。
 */
data class Account(
    val userName: String,
    var password: String,
    var remark: String = "",
    /** 所属云端配置；没有配置的老账号归到 legacy。 */
    var profileId: String = CloudProfile.LEGACY_ID,
)

/**
 * 账号列表 + 当前账号 + 每个账号的会话 Cookie。
 *
 * 会话按账号分开存：切换账号不会顶掉另一个账号的登录态，
 * 与桌面端「一个账号一个会话」的语义保持一致。
 */
class AccountStore(context: Context) {

    private val prefs = context.getSharedPreferences("accounts", Context.MODE_PRIVATE)

    /** 本机全部账号，含各配置；只有迁移/诊断才用。 */
    var allAccounts: List<Account>
        get() = runCatching {
            val raw = prefs.getString(KEY_ACCOUNTS, null) ?: return emptyList()
            val arr = JSONArray(raw)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                Account(
                    userName = o.optString("userName"),
                    password = o.optString("password"),
                    remark = o.optString("remark"),
                    profileId = o.optString("profileId").ifBlank { CloudProfile.LEGACY_ID },
                )
            }.filter { it.userName.isNotBlank() }
        }.getOrDefault(emptyList())
        set(value) = prefs.edit {
            val arr = JSONArray()
            value.forEach { a ->
                arr.put(
                    JSONObject()
                        .put("userName", a.userName)
                        .put("password", a.password)
                        .put("remark", a.remark)
                        .put("profileId", a.profileId)
                )
            }
            putString(KEY_ACCOUNTS, arr.toString())
        }

    /** 当前配置下的账号；没有配置档案时用 legacy，老账号照常可见。 */
    val accounts: List<Account>
        get() {
            val scope = CloudProfiles.activeId.let {
                if (it.isBlank()) CloudProfile.LEGACY_ID else it
            }
            return allAccounts.filter { it.profileId == scope }
        }

    val activeUserName: String
        get() {
            val key = "$KEY_ACTIVE:${scope()}"
            return prefs.getString(key, "").orEmpty()
        }

    val active: Account?
        get() = accounts.firstOrNull { it.userName == activeUserName }

    fun upsert(account: Account) {
        val scoped = account.copy(profileId = scope())
        val list = allAccounts.toMutableList()
        val index = list.indexOfFirst { it.userName == scoped.userName && it.profileId == scoped.profileId }
        if (index >= 0) list[index] = scoped else list.add(scoped)
        allAccounts = list
        setActive(scoped.userName)
    }

    fun setActive(userName: String) {
        prefs.edit { putString("$KEY_ACTIVE:${scope()}", userName) }
    }

    fun remove(userName: String) {
        allAccounts = allAccounts.filterNot { it.userName == userName && it.profileId == scope() }
        prefs.edit { remove(cookieKey(userName)) }
        if (activeUserName == userName) {
            setActive(accounts.firstOrNull()?.userName.orEmpty())
        }
    }

    /** 删除云端配置时连它名下的账号与会话一起清掉，不留孤儿数据。 */
    fun removeProfile(profileId: String) {
        allAccounts.filter { it.profileId == profileId }.forEach {
            prefs.edit { remove(cookieKey(it.userName)) }
        }
        allAccounts = allAccounts.filterNot { it.profileId == profileId }
    }

    private fun scope(): String = CloudProfiles.activeId.ifBlank { CloudProfile.LEGACY_ID }

    fun cookieHeader(userName: String): String = prefs.getString(cookieKey(userName), "").orEmpty()

    fun saveCookies(userName: String, header: String) {
        prefs.edit { putString(cookieKey(userName), header) }
    }

    fun clearCookies(userName: String) {
        prefs.edit { remove(cookieKey(userName)) }
    }

    private companion object {
        const val KEY_ACCOUNTS = "accounts_json"
        const val KEY_ACTIVE = "active_user"
        fun cookieKey(userName: String) = "cookie:$userName"
    }
}
