package com.sakaiya.news

import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class ConfigActivity : AppCompatActivity() {

    private var widgetId = AppWidgetManager.INVALID_APPWIDGET_ID
    private var isWidgetConfig = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_config)

        widgetId = intent.extras?.getInt(
            AppWidgetManager.EXTRA_APPWIDGET_ID,
            AppWidgetManager.INVALID_APPWIDGET_ID
        ) ?: AppWidgetManager.INVALID_APPWIDGET_ID

        isWidgetConfig = widgetId != AppWidgetManager.INVALID_APPWIDGET_ID

        if (isWidgetConfig) {
            setResult(RESULT_CANCELED)
        }

        val input = findViewById<EditText>(R.id.api_key_input)
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
                Toast.makeText(this, "APIキーを入力してください", Toast.LENGTH_SHORT).show()
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
            Toast.makeText(this, "保存しました", Toast.LENGTH_SHORT).show()

            if (isWidgetConfig) {
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
}
