package com.sakaiya.news

import android.content.Context
import android.content.SharedPreferences
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.*

data class NewsItem(
    val title: String,
    val snippet: String,
    val source: String,
    val url: String,
    val time: String
)

object NewsData {
    private const val PREFS = "sakaiya_news"
    private const val KEY_API = "api_key"
    private const val KEY_CACHE = "cache_json"
    private const val KEY_TIME = "cache_time"
    private const val KEY_KEYWORD = "keyword"

    val KEYWORDS = listOf("ニュース", "サッカー", "プロレス")
    private val SPORTS_KW = setOf("サッカー", "プロレス")

    private val client = OkHttpClient.Builder()
        .callTimeout(java.time.Duration.ofSeconds(35))
        .build()

    fun prefs(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun getApiKey(ctx: Context): String =
        prefs(ctx).getString(KEY_API, "") ?: ""

    fun setApiKey(ctx: Context, key: String) =
        prefs(ctx).edit().putString(KEY_API, key).apply()

    fun getKeyword(ctx: Context): String =
        prefs(ctx).getString(KEY_KEYWORD, "ニュース") ?: "ニュース"

    fun setKeyword(ctx: Context, kw: String) =
        prefs(ctx).edit().putString(KEY_KEYWORD, kw).apply()

    fun getCachedNews(ctx: Context): List<NewsItem> {
        val kw = getKeyword(ctx)
        val json = prefs(ctx).getString("${KEY_CACHE}_$kw", null) ?: return emptyList()
        return parseItems(JSONArray(json))
    }

    fun getCacheTime(ctx: Context): String {
        val kw = getKeyword(ctx)
        val ms = prefs(ctx).getLong("${KEY_TIME}_$kw", 0)
        if (ms == 0L) return ""
        return SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(ms))
    }

    fun fetchNews(ctx: Context): List<NewsItem> {
        val apiKey = getApiKey(ctx)
        if (apiKey.isBlank()) return emptyList()

        val kw = getKeyword(ctx)
        val isSports = kw in SPORTS_KW
        val prompt = buildPrompt(kw, isSports)

        val body = JSONObject().apply {
            put("model", "claude-sonnet-4-6")
            put("max_tokens", 1200)
            put("tools", JSONArray().put(JSONObject().apply {
                put("type", "web_search_20250305")
                put("name", "web_search")
            }))
            put("messages", JSONArray().put(JSONObject().apply {
                put("role", "user")
                put("content", prompt)
            }))
        }

        val request = Request.Builder()
            .url("https://api.anthropic.com/v1/messages")
            .addHeader("Content-Type", "application/json")
            .addHeader("x-api-key", apiKey)
            .addHeader("anthropic-version", "2023-06-01")
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build()

        val response = client.newCall(request).execute()
        if (!response.isSuccessful) return getCachedNews(ctx)

        val respBody = response.body?.string() ?: return getCachedNews(ctx)
        val content = JSONObject(respBody).getJSONArray("content")
        val textParts = StringBuilder()
        for (i in 0 until content.length()) {
            val block = content.getJSONObject(i)
            if (block.getString("type") == "text") {
                textParts.append(block.getString("text"))
            }
        }

        val cleaned = textParts.toString().replace(Regex("```json|```"), "").trim()
        val match = Regex("\\[\\s*\\{[\\s\\S]*}\\s*]").find(cleaned) ?: return getCachedNews(ctx)
        val items = parseItems(JSONArray(match.value))

        prefs(ctx).edit()
            .putString("${KEY_CACHE}_$kw", match.value)
            .putLong("${KEY_TIME}_$kw", System.currentTimeMillis())
            .apply()

        return items
    }

    private fun buildPrompt(keyword: String, isSports: Boolean): String {
        val sources = if (isSports) {
            "Reuters (en), tagesschau (de)"
        } else {
            "Reuters (en), 時事通信 (ja), tagesschau (de), 日経新聞 (ja)"
        }

        val searchLang = if (isSports) {
            "Search each source in its native language (English for Reuters, German for tagesschau)."
        } else {
            "Search each source in its native language (English for Reuters, Japanese for 時事通信 and 日経新聞, German for tagesschau)."
        }

        val topic = if (keyword == "ニュース") {
            "top breaking news"
        } else {
            "latest $keyword news"
        }

        return """Search for today's $topic from each of these sources: $sources.
For each source, find 1-2 of their most important current headlines about $topic.
$searchLang
Return ONLY a JSON array of up to 6 items, newest first.
Each item: {"title":"...","snippet":"1 sentence summary in the article's original language","source":"publication name","url":"article URL","time":"relative time or date"}.
Keep titles and snippets in the original language of the article. No markdown, no backticks, just the JSON array."""
    }

    private fun parseItems(arr: JSONArray): List<NewsItem> {
        val list = mutableListOf<NewsItem>()
        for (i in 0 until arr.length()) {
            val obj = arr.getJSONObject(i)
            list.add(NewsItem(
                title = obj.optString("title", ""),
                snippet = obj.optString("snippet", ""),
                source = obj.optString("source", ""),
                url = obj.optString("url", ""),
                time = obj.optString("time", "")
            ))
        }
        return list
    }
}
