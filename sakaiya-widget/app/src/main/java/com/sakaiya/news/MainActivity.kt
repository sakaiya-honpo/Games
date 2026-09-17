package com.sakaiya.news

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {

    private lateinit var recycler: RecyclerView
    private lateinit var statusText: TextView
    private lateinit var kwLabel: TextView
    private lateinit var setupPrompt: LinearLayout
    private val adapter = NewsAdapter()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        recycler = findViewById(R.id.news_list)
        statusText = findViewById(R.id.main_status)
        kwLabel = findViewById(R.id.keyword_label)
        setupPrompt = findViewById(R.id.setup_prompt)

        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = adapter

        findViewById<View>(R.id.btn_settings).setOnClickListener {
            startActivity(Intent(this, ConfigActivity::class.java))
        }

        findViewById<View>(R.id.btn_setup).setOnClickListener {
            startActivity(Intent(this, ConfigActivity::class.java))
        }

        findViewById<View>(R.id.btn_refresh).setOnClickListener {
            loadNews()
        }
    }

    override fun onResume() {
        super.onResume()
        val kw = NewsData.getKeyword(this)
        kwLabel.text = kw

        if (NewsData.getApiKey(this).isBlank()) {
            setupPrompt.visibility = View.VISIBLE
            recycler.visibility = View.GONE
            statusText.text = ""
            return
        }

        setupPrompt.visibility = View.GONE
        recycler.visibility = View.VISIBLE

        val cached = NewsData.getCachedNews(this)
        if (cached.isNotEmpty()) {
            adapter.items = cached
            adapter.notifyDataSetChanged()
            statusText.text = "最終更新: ${NewsData.getCacheTime(this)}"
        } else {
            loadNews()
        }
    }

    private fun loadNews() {
        val apiKey = NewsData.getApiKey(this)
        if (apiKey.isBlank()) return

        statusText.text = "読み込み中..."
        thread {
            val items = NewsData.fetchNews(applicationContext)
            runOnUiThread {
                if (items.isNotEmpty()) {
                    adapter.items = items
                    adapter.notifyDataSetChanged()
                    statusText.text = "最終更新: ${NewsData.getCacheTime(this)}"
                    NewsWidgetProvider.updateAll(this)
                } else {
                    statusText.text = "取得失敗 - 設定を確認してください"
                }
            }
        }
    }
}

class NewsAdapter : RecyclerView.Adapter<NewsAdapter.VH>() {
    var items: List<NewsItem> = emptyList()

    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val title: TextView = view.findViewById(R.id.news_title)
        val snippet: TextView = view.findViewById(R.id.news_snippet)
        val source: TextView = view.findViewById(R.id.news_source)
        val time: TextView = view.findViewById(R.id.news_time)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_news, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        holder.title.text = item.title
        holder.snippet.text = item.snippet
        holder.source.text = item.source
        holder.time.text = item.time

        val color = when {
            item.source.lowercase().contains("reuters") -> 0xFFFF8C00.toInt()
            item.source.contains("時事") -> 0xFF2DD4BF.toInt()
            item.source.lowercase().contains("tagesschau") -> 0xFF3B82F6.toInt()
            item.source.contains("日経") -> 0xFFA78BFA.toInt()
            else -> 0xFF6366F1.toInt()
        }
        holder.source.setTextColor(color)

        holder.itemView.setOnClickListener {
            if (item.url.isNotBlank()) {
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(item.url))
                holder.itemView.context.startActivity(intent)
            }
        }
    }

    override fun getItemCount(): Int = items.size
}
