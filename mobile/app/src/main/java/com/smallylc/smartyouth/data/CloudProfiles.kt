package com.smallylc.smartyouth.data

import android.content.Context
import androidx.core.content.edit
import com.smallylc.smartyouth.app
import org.json.JSONArray
import org.json.JSONObject

/**
 * 一套云端配置 = 一个提取码 + 服务地址 + 密码，对应电脑上的一份备份。
 * 不同配置带各自的支部账号，切换配置时只显示该配置的账号。
 * 对应桌面端 cloud_backup.py 的 cloudProfiles。
 */
data class CloudProfile(
    val id: String,
    val name: String,
    val server: String,
    val code: String,
    val password: String,
) {
    companion object {
        /** 没有配置档案的老账号归到这一套，保证升级后不丢数据。 */
        const val LEGACY_ID = "legacy"
    }
}

/**
 * 配置档案列表与当前配置。密码只落本机私有目录，与站点账号一样明文存储。
 */
class CloudProfileStore(context: Context) {

    private val prefs = context.getSharedPreferences("cloud_profiles", Context.MODE_PRIVATE)

    var profiles: List<CloudProfile>
        get() = runCatching {
            val raw = prefs.getString(KEY_PROFILES, null) ?: return emptyList()
            val arr = JSONArray(raw)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                CloudProfile(
                    id = o.optString("id"),
                    name = o.optString("name"),
                    server = o.optString("server"),
                    code = o.optString("code"),
                    password = o.optString("password"),
                )
            }.filter { it.id.isNotBlank() && it.code.isNotBlank() }
        }.getOrDefault(emptyList())
        set(value) = prefs.edit {
            val arr = JSONArray()
            value.forEach { p ->
                arr.put(
                    JSONObject()
                        .put("id", p.id)
                        .put("name", p.name)
                        .put("server", p.server)
                        .put("code", p.code)
                        .put("password", p.password)
                )
            }
            putString(KEY_PROFILES, arr.toString())
        }

    var activeId: String
        get() = prefs.getString(KEY_ACTIVE, "").orEmpty()
        set(value) = prefs.edit { putString(KEY_ACTIVE, value) }

    val active: CloudProfile? get() = profiles.firstOrNull { it.id == activeId }

    /** 同一提取码 + 服务地址视为同一配置，重复同步是更新而不是新增。 */
    fun save(profile: CloudProfile): CloudProfile {
        val existing = profiles.firstOrNull {
            it.code == profile.code && it.server == profile.server
        }
        val saved = if (existing != null) existing.copy(
            name = profile.name.ifBlank { existing.name },
            password = profile.password,
        ) else profile
        profiles = if (existing != null) {
            profiles.map { if (it.id == saved.id) saved else it }
        } else {
            profiles + saved
        }
        activeId = saved.id
        return saved
    }

    fun remove(id: String) {
        profiles = profiles.filterNot { it.id == id }
        if (activeId == id) activeId = profiles.firstOrNull()?.id.orEmpty()
    }

    private companion object {
        const val KEY_PROFILES = "profiles_json"
        const val KEY_ACTIVE = "active_profile"
    }
}

val CloudProfiles by lazy { CloudProfileStore(app) }
