package com.smallylc.smartyouth.update

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest

/**
 * 版本比对是更新链路的闸门：误报“有新版本”会诱导用户下载装不上的包，
 * 漏报则让老版本永远停在原地，所以逐位比较的边界都要钉死。
 */
class GitHubUpdateIsNewerTest {

    @Test
    fun higherMinorAndPatchAreNewer() {
        assertTrue(GitHubUpdate.isNewer("2.1.0", "2.0.0"))
        assertTrue(GitHubUpdate.isNewer("2.0.1", "2.0.0"))
    }

    @Test
    fun equalVersionsAreNotNewer() {
        assertFalse(GitHubUpdate.isNewer("2.0.0", "2.0.0"))
    }

    @Test
    fun olderRemoteIsNotNewer() {
        assertFalse(GitHubUpdate.isNewer("1.9.9", "2.0.0"))
        assertFalse(GitHubUpdate.isNewer("2.0.0", "2.0.1"))
    }

    @Test
    fun digitCountDifferencePadsWithZero() {
        assertTrue(GitHubUpdate.isNewer("2.0.0.1", "2.0.0"))
        assertFalse(GitHubUpdate.isNewer("2.0.0", "2.0.0.1"))
        assertFalse(GitHubUpdate.isNewer("2.0", "2.0.0"))
    }

    @Test
    fun multiDigitComponentsCompareNumerically() {
        // 字符串比较会判 "10" < "9"，数字比较必须判 10 > 9
        assertTrue(GitHubUpdate.isNewer("2.10.0", "2.9.0"))
        assertTrue(GitHubUpdate.isNewer("10.0.0", "9.9.9"))
    }

    private fun sha256Of(s: String) = MessageDigest.getInstance("SHA-256")
        .digest(s.toByteArray()).joinToString("") { "%02x".format(it) }

    @Test
    fun sha256MatchesKnownDigest() {
        val f = File.createTempFile("apk-digest", ".bin")
        f.writeBytes("hello".toByteArray())
        assertTrue(GitHubUpdate.sha256(f).equals(sha256Of("hello"), ignoreCase = true))
        f.delete()
    }

    @Test
    fun mismatchedDigestRejectsTamperedMirror() {
        val f = File.createTempFile("apk-digest", ".bin")
        f.writeBytes("tampered".toByteArray())
        val info = GitHubUpdate.Info("2.2.0", "", "http://x/a.apk", 8, sha256Of("original"))
        assertFalse(GitHubUpdate.digestOk(f, info))
        f.delete()
    }

    @Test
    fun matchingDigestPasses() {
        val f = File.createTempFile("apk-digest", ".bin")
        f.writeBytes("original".toByteArray())
        val info = GitHubUpdate.Info("2.2.0", "", "http://x/a.apk", 8, sha256Of("original"))
        assertTrue(GitHubUpdate.digestOk(f, info))
        f.delete()
    }

    @Test
    fun missingDigestFallsBackToSize() {
        val f = File.createTempFile("apk-digest", ".bin")
        f.writeBytes("12345678".toByteArray())
        val info = GitHubUpdate.Info("2.2.0", "", "http://x/a.apk", 8, "")
        assertTrue(GitHubUpdate.digestOk(f, info))
        assertFalse(GitHubUpdate.digestOk(f, info.copy(size = 9)))
        f.delete()
    }
}
