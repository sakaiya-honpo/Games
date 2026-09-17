package com.sakaiya.news

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.RemoteViews
import androidx.work.*
import java.util.concurrent.TimeUnit

class NewsWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) {
        for (id in ids) updateWidget(ctx, mgr, id)
        enqueuePeriodicWork(ctx)
    }

    override fun onEnabled(ctx: Context) {
        enqueuePeriodicWork(ctx)
    }

    override fun onDisabled(ctx: Context) {
        WorkManager.getInstance(ctx).cancelUniqueWork("sakaiya_refresh")
    }

    companion object {
        fun updateWidget(ctx: Context, mgr: AppWidgetManager, widgetId: Int) {
            val intent = Intent(ctx, NewsWidgetService::class.java).apply {
                putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId)
                data = Uri.parse(toUri(Intent.URI_INTENT_SCHEME))
            }

            val kw = NewsData.getKeyword(ctx)
            val title = if (kw == "ニュース") "Sakaiya News" else "Sakaiya News / $kw"
            val views = RemoteViews(ctx.packageName, R.layout.widget_layout).apply {
                setRemoteAdapter(R.id.widget_list, intent)
                setEmptyView(R.id.widget_list, R.id.widget_empty)
                setTextViewText(R.id.widget_title, title)
                setTextViewText(R.id.widget_time, NewsData.getCacheTime(ctx))
            }

            val clickIntent = Intent(Intent.ACTION_VIEW)
            val clickPending = PendingIntent.getActivity(
                ctx, 0, clickIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
            )
            views.setPendingIntentTemplate(R.id.widget_list, clickPending)

            mgr.updateAppWidget(widgetId, views)
            mgr.notifyAppWidgetViewDataChanged(widgetId, R.id.widget_list)
        }

        fun updateAll(ctx: Context) {
            val mgr = AppWidgetManager.getInstance(ctx)
            val ids = mgr.getAppWidgetIds(ComponentName(ctx, NewsWidgetProvider::class.java))
            for (id in ids) updateWidget(ctx, mgr, id)
        }

        private fun enqueuePeriodicWork(ctx: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val work = PeriodicWorkRequestBuilder<RefreshWorker>(30, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build()

            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
                "sakaiya_refresh",
                ExistingPeriodicWorkPolicy.KEEP,
                work
            )
        }
    }
}

class RefreshWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        NewsData.fetchNews(applicationContext)
        NewsWidgetProvider.updateAll(applicationContext)
        return Result.success()
    }
}
