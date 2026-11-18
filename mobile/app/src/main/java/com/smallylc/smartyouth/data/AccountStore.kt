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
)

/**
 * 账号列表 + 当前账号 + 每个账号的会话 Cookie。
 *
 * 会话按账号分开存：切换账号不会顶掉另一个账号的登录态，
 * 与桌面端「一个账号一个会话」的语义保持一致。
 */
class AccountStore(context: Context) {

    private val prefs = context.getSharedPreferences("accounts", Context.MODE_PRIVATE)

    var accounts: List<Account>
        get() = runCatching {
            val raw = prefs.getString(KEY_ACCOUNTS, null) ?: return emptyList()
            val arr = JSONArray(raw)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                Account(
                    userName = o.optString("userName"),
                    password = o.optString("password"),
                    remark = o.optString("remark"),
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
                )
            }
            putString(KEY_ACCOUNTS, arr.toString())
        }

    var activeUserName: String
        get() = prefs.getString(KEY_ACTIVE, "").orEmpty()
        set(value) = prefs.edit { putString(KEY_ACTIVE, value) }

    val active: Account?
        get() = accounts.firstOrNull { it.userName == activeUserName }

    fun upsert(account: Account) {
        val list = accounts.toMutableList()
        val index = list.indexOfFirst { it.userName == account.userName }
        if (index >= 0) list[index] = account else list.add(account)
        accounts = list
        activeUserName = account.userName
    }

    fun remove(userName: String) {
        accounts = accounts.filterNot { it.userName == userName }
        prefs.edit { remove(cookieKey(userName)) }
        if (activeUserName == userName) {
            activeUserName = accounts.firstOrNull()?.userName.orEmpty()
        }
    }

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
