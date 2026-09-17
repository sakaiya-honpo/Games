package com.sakaiya.news

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.RemoteViews
import android.widget.RemoteViewsService

class NewsWidgetService : RemoteViewsService() {
    override fun onGetViewFactory(intent: Intent): RemoteViewsFactory {
        return NewsViewsFactory(applicationContext)
    }
}

class NewsViewsFactory(private val ctx: Context) : RemoteViewsService.RemoteViewsFactory {

    private var items: List<NewsItem> = emptyList()

    override fun onCreate() {}

    override fun onDataSetChanged() {
        items = NewsData.getCachedNews(ctx)
    }

    override fun onDestroy() {}

    override fun getCount(): Int = items.size

    override fun getViewAt(position: Int): RemoteViews {
        val item = items[position]
        val views = RemoteViews(ctx.packageName, R.layout.widget_item)

        views.setTextViewText(R.id.item_title, item.title)
        views.setTextViewText(R.id.item_snippet, item.snippet)
        views.setTextViewText(R.id.item_source, item.source)
        views.setTextViewText(R.id.item_time, item.time)

        val color = when {
            item.source.lowercase().contains("reuters") -> 0xFFFF8C00.toInt()
            item.source.contains("時事") -> 0xFF2DD4BF.toInt()
            item.source.lowercase().contains("tagesschau") -> 0xFF3B82F6.toInt()
            item.source.contains("日経") -> 0xFFA78BFA.toInt()
            else -> 0xFF6366F1.toInt()
        }
        views.setTextColor(R.id.item_source, color)

        val fillIntent = Intent().apply {
            data = Uri.parse(item.url)
        }
        views.setOnClickFillInIntent(R.id.item_root, fillIntent)

        return views
    }

    override fun getLoadingView(): RemoteViews? = null
    override fun getViewTypeCount(): Int = 1
    override fun getItemId(position: Int): Long = position.toLong()
    override fun hasStableIds(): Boolean = false
}
