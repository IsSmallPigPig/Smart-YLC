package com.smallylc.smartyouth.update

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

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
}
