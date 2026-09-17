package com.sakaiya.news

import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlin.concurrent.thread

class ConfigActivity : AppCompatActivity() {

    private var widgetId = AppWidgetManager.INVALID_APPWIDGET_ID

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setResult(RESULT_CANCELED)
        setContentView(R.layout.activity_config)

        widgetId = intent.extras?.getInt(
            AppWidgetManager.EXTRA_APPWIDGET_ID,
            AppWidgetManager.INVALID_APPWIDGET_ID
        ) ?: AppWidgetManager.INVALID_APPWIDGET_ID

        val input = findViewById<EditText>(R.id.api_key_input)
        val status = findViewById<TextView>(R.id.status_text)
        val kwGroup = findViewById<RadioGroup>(R.id.keyword_group)

        input.setText(NewsData.getApiKey(this))

        val currentKw = NewsData.getKeyword(this)
        NewsData.KEYWORDS.forEachIndexed { index, kw ->
            val rb = kwGroup.getChildAt(index) as? RadioButton
            rb?.isChecked = (kw == currentKw)
        }

        findViewById<Button>(R.id.save_btn).setOnClickListener {
            val key = input.text.toString().trim()
            if (key.isBlank()) {
                status.text = "APIキーを入力してください"
                return@setOnClickListener
            }

            val selectedIdx = kwGroup.indexOfChild(kwGroup.findViewById(kwGroup.checkedRadioButtonId))
            val keyword = if (selectedIdx in NewsData.KEYWORDS.indices) {
                NewsData.KEYWORDS[selectedIdx]
            } else {
                "ニュース"
            }

            NewsData.setApiKey(this, key)
            NewsData.setKeyword(this, keyword)
            status.text = "「$keyword」のニュース取得中..."

            thread {
                val items = NewsData.fetchNews(applicationContext)
                runOnUiThread {
                    if (items.isNotEmpty()) {
                        status.text = "${items.size}件のニュースを取得しました"
                        finishWithWidget()
                    } else {
                        status.text = "取得失敗 - APIキーを確認してください"
                    }
                }
            }
        }
    }

    private fun finishWithWidget() {
        if (widgetId != AppWidgetManager.INVALID_APPWIDGET_ID) {
            NewsWidgetProvider.updateWidget(
                this,
                AppWidgetManager.getInstance(this),
                widgetId
            )
            setResult(RESULT_OK, Intent().putExtra(
                AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId
            ))
        } else {
            NewsWidgetProvider.updateAll(this)
        }
        finish()
    }
}
