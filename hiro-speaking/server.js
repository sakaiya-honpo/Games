const express = require('express');
const Anthropic = require('@anthropic-ai/sdk');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const ENV_FILE = path.join(__dirname, '.env');
const DATA_FILE = path.join(__dirname, 'data.json');

function loadEnv() {
  try {
    if (fs.existsSync(ENV_FILE)) {
      const lines = fs.readFileSync(ENV_FILE, 'utf8').split('\n');
      for (const line of lines) {
        const match = line.match(/^([^#=]+)=(.*)$/);
        if (match) process.env[match[1].trim()] = match[2].trim();
      }
    }
  } catch (e) {}
}
loadEnv();

function getClient() {
  return new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
}

const STAGE_NAMES = { 1: 'A2', 2: 'A2+', 3: 'B1-', 4: 'B1+', 5: 'B2' };
const STAGE_THRESHOLDS = [
  null, null,
  { phrases: 50, correctionRate: 0.6, avgWords: 0 },
  { phrases: 120, correctionRate: 0.5, avgWords: 6 },
  { phrases: 200, correctionRate: 0.4, avgWords: 8 },
  { phrases: 300, correctionRate: 0.3, avgWords: 10 }
];

const TOPICS = {
  food:       { name: '食事', keywords: '食事、レストラン、料理、食べ物、飲み物' },
  weather:    { name: '天気', keywords: '天気、季節、気候、気温' },
  daily:      { name: '日常', keywords: '日課、日常生活、最近あったこと' },
  shopping:   { name: '買い物', keywords: '買い物、店、値段' },
  hobbies:    { name: '趣味', keywords: '趣味、好きなこと、自由時間' },
  weekend:    { name: '週末', keywords: '週末の予定、休日の過ごし方' },
  travel:     { name: '旅行', keywords: '旅行、観光、行きたい場所' },
  work:       { name: '仕事', keywords: '仕事、職場、キャリア' },
  future:     { name: '将来', keywords: '将来の計画、夢、目標' },
  technology: { name: 'テクノロジー', keywords: 'AI、テクノロジー、インターネット' },
  environment:{ name: '環境', keywords: '環境問題、エコ、持続可能性' },
  education:  { name: '教育', keywords: '教育、学校、学び方' },
  culture:    { name: '文化', keywords: '文化の違い、異文化理解、海外生活' },
  media:      { name: 'メディア', keywords: 'SNS、メディア、ニュース、情報' },
  society:    { name: '社会', keywords: '社会問題、政策、公共' }
};

const TOPIC_STAGES = {
  1: ['food', 'weather', 'daily', 'shopping'],
  2: ['hobbies', 'weekend', 'travel'],
  3: ['work', 'future'],
  4: ['technology', 'environment', 'education'],
  5: ['culture', 'media', 'society']
};

function getAvailableTopicIds(stage) {
  const ids = [];
  for (let s = 1; s <= stage; s++) {
    if (TOPIC_STAGES[s]) ids.push(...TOPIC_STAGES[s]);
  }
  return ids;
}

// ─── Data persistence ───
function loadData() {
  try {
    if (fs.existsSync(DATA_FILE)) return JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
  } catch (e) {}
  return {
    phrases: [],
    stage: 1,
    recentUtterances: [],
    totalConversations: 0,
    practiceDays: [],
    stageHistory: [{ stage: 1, reachedAt: Date.now() }]
  };
}

function saveData(data) {
  fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2));
}

// ─── Conversation history (in-memory, per topic session) ───
let conversationMessages = [];

// ─── Stage-specific system prompt ───
function buildSystemPrompt(data, topicId) {
  const stage = data.stage;
  const topicInfo = TOPICS[topicId];

  const stageInstructions = {
    1: `【Stage 1（A2）の指示】
- 質問は事実質問のみ（What / Where / When / Who）
- 話題：食事、天気、家族、買い物、日課
- 語彙：Oxford 3000 A2レベル
- 修正対象：冠詞、前置詞、基本時制
- 紹介するフレーズ：短い定型表現（I had a... / I went to... / It was...）
- 1ターンに質問は1つだけ`,
    2: `【Stage 2（A2+）の指示】
- 質問は事実質問＋軽い理由（Why did you...? / Do you like...?）
- 話題：食事、天気、日常、買い物＋趣味、週末、旅行の思い出
- 語彙：Oxford 3000 A2〜B1（A2中心）
- 修正対象：冠詞、前置詞、基本時制＋語順、三単現、不規則過去形
- 紹介するフレーズ：少し長い表現（I usually... / I'm thinking about... / It depends on...）`,
    3: `【Stage 3（B1-）の指示】
- 質問は理由・比較（Why do you think...? / Which do you prefer...?）
- 話題：＋仕事、将来の計画、経験したこと
- 語彙：Oxford 3000 B1中心
- 修正対象：＋接続詞の使い方、時制の一貫性
- 紹介するフレーズ：接続表現（On the other hand... / The reason is... / I've never...）`,
    4: `【Stage 4（B1+）の指示】
- 質問は意見・仮定（What would you do if...? / Do you agree that...?）
- 話題：＋社会的な話題（環境、テクノロジー、教育）
- 語彙：Oxford 3000 B1〜B2（B1中心）
- 修正対象：＋仮定法、関係詞、受動態
- 紹介するフレーズ：意見表現（I'd say that... / It seems to me... / That's a good point, but...）`,
    5: `【Stage 5（B2）の指示】
- 質問は議論・分析（How has X changed...? / What are the advantages and disadvantages?）
- 話題：抽象的なテーマ（文化の違い、メディアの影響、公共政策）
- 語彙：Oxford 3000 B2
- 修正対象：全レベル＋より洗練された表現への言い換え
- 紹介するフレーズ：議論表現（From my perspective... / Having said that... / It's worth considering...）`
  };

  return `あなたはフレンドリーな英会話の練習相手です。
ユーザーはCEFR ${STAGE_NAMES[stage]}レベルの日本語話者で、ドイツ在住です。

【メソッド：だいじろー式「フレーズの引き出しを増やす」】
ゼロから文法を組み立てるのではなく、フレーズの塊をたくさん覚えて引き出して投げる練習。

【最重要ルール】
1. ユーザーの英語が不完全でも、まず「伝わりましたよ」と日本語で肯定する。いきなり修正しない。
2. その上で「自然な言い方」を1〜3個、具体的なフレーズとして提示する。フレーズは「塊」として覚えられる形で。
3. なぜその表現が自然かを日本語で短く（1〜2文）説明する。文法用語は最小限。
4. 会話を途切れさせない。必ず次の質問を英語で1つ投げる。
5. ユーザーが日本語混じりで答えてもOK。意味を汲み取る。
6. 長い解説はしない。

【ユーザーのよくあるエラーパターン（特に注意して修正）】
- 冠詞の脱落（joined summer course → a summer course）
- toの脱落（I want go → I want to go）
- 助動詞+過去形（can't went → can't go）
- there/that混同
- stuff/staff混同
- 動詞なし文（I thinking → I am thinking）
- 否定の形（there is no many → there are not many）

【定着済み（修正不要）】
- be動詞と一般動詞の否定の区別
- 助動詞＋原形
- 基本的な句動詞

【現在のステージ】Stage ${stage}（${STAGE_NAMES[stage]}）
${stageInstructions[stage]}

【現在の会話トピック】${topicInfo ? topicInfo.name + '（' + topicInfo.keywords + '）' : '自由'}

【応答形式】
必ず以下のJSON形式のみで応答してください。JSON以外のテキストは含めないでください。
{
  "affirmation": "伝わったことの確認（日本語で1文）",
  "corrections": [{"orig": "ユーザーの元の表現", "fixed": "修正後の表現", "note": "修正理由（日本語で短く）"}],
  "phrases": [{"en": "自然な英語フレーズ", "note": "短い説明（日本語）"}],
  "point": "ポイント解説（日本語・1〜2文。フレーズの塊として覚えるコツ）",
  "followUp": "会話を続ける質問（英語で1つ）"
}

corrections はユーザーの英語に修正が必要な場合のみ。修正不要なら空配列 [] にしてください。
phrases は1〜3個。ユーザーの発話に関連する自然な表現。
ユーザーが文法的に正しい自然な英語を使った場合は、affirmationでしっかり褒めて、correctionsは空配列にしてください。`;
}

function buildStartPrompt(data, topicId) {
  const stage = data.stage;
  const topicInfo = TOPICS[topicId];

  return `あなたはフレンドリーな英会話の練習相手です。
ユーザーはCEFR ${STAGE_NAMES[stage]}レベルの日本語話者です。

今から「${topicInfo.name}」の話題で会話を始めます。

Stage ${stage}（${STAGE_NAMES[stage]}）に合った質問を英語で1つだけ投げてください。
${stage <= 2 ? '事実を聞くシンプルな質問（What / Where / When / Who）にしてください。' : ''}
${stage === 3 ? '理由や比較を聞く質問にしてください。' : ''}
${stage === 4 ? '意見や仮定を聞く質問にしてください。' : ''}
${stage === 5 ? '分析や議論を促す質問にしてください。' : ''}

英語の質問文だけを返してください。JSON不要。日本語不要。`;
}

// ─── Stage check ───
function checkStageUp(data) {
  if (data.stage >= 5) return false;
  const next = data.stage + 1;
  const req = STAGE_THRESHOLDS[next];
  if (!req) return false;

  const phraseCount = data.phrases.length;
  const recent = data.recentUtterances;
  const corrRate = recent.length === 0 ? 1 : recent.filter(u => u.wasCorrected).length / recent.length;
  const avgWords = recent.length === 0 ? 0 : recent.reduce((a, u) => a + u.wordCount, 0) / recent.length;

  if (phraseCount >= req.phrases && corrRate <= req.correctionRate && (req.avgWords === 0 || avgWords >= req.avgWords)) {
    data.stage = next;
    data.stageHistory.push({ stage: next, reachedAt: Date.now() });
    saveData(data);
    return true;
  }
  return false;
}

// ─── API Routes ───

app.get('/api/config', (req, res) => {
  const hasKey = !!(process.env.ANTHROPIC_API_KEY && process.env.ANTHROPIC_API_KEY.startsWith('sk-'));
  res.json({ hasApiKey: hasKey });
});

app.post('/api/config', (req, res) => {
  const { apiKey } = req.body;
  if (!apiKey || !apiKey.startsWith('sk-')) {
    return res.status(400).json({ error: 'Invalid API key format. It should start with sk-' });
  }
  try {
    let envContent = '';
    if (fs.existsSync(ENV_FILE)) {
      envContent = fs.readFileSync(ENV_FILE, 'utf8');
      if (envContent.match(/^ANTHROPIC_API_KEY=.*/m)) {
        envContent = envContent.replace(/^ANTHROPIC_API_KEY=.*/m, `ANTHROPIC_API_KEY=${apiKey}`);
      } else {
        envContent += `\nANTHROPIC_API_KEY=${apiKey}`;
      }
    } else {
      envContent = `ANTHROPIC_API_KEY=${apiKey}\nPORT=${process.env.PORT || 3000}\n`;
    }
    fs.writeFileSync(ENV_FILE, envContent);
    process.env.ANTHROPIC_API_KEY = apiKey;
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: 'Failed to save API key' });
  }
});

app.get('/api/data', (req, res) => {
  const data = loadData();
  const availableTopics = getAvailableTopicIds(data.stage).map(id => ({
    id,
    name: TOPICS[id].name
  }));
  res.json({ ...data, availableTopics });
});

app.post('/api/phrases', (req, res) => {
  const data = loadData();
  const { action, phrase, index } = req.body;

  if (action === 'save' && phrase) {
    if (!data.phrases.some(p => p.en === phrase.en)) {
      data.phrases.push({ ...phrase, savedAt: Date.now() });
      saveData(data);
      const stageUp = checkStageUp(data);
      return res.json({ ok: true, phraseCount: data.phrases.length, stageUp, stage: data.stage });
    }
    return res.json({ ok: true, phraseCount: data.phrases.length, alreadySaved: true });
  }

  if (action === 'delete' && typeof index === 'number') {
    data.phrases.splice(index, 1);
    saveData(data);
    return res.json({ ok: true, phraseCount: data.phrases.length });
  }

  res.status(400).json({ error: 'invalid action' });
});

app.post('/api/start', async (req, res) => {
  const { topicId } = req.body;
  if (!TOPICS[topicId]) return res.status(400).json({ error: 'invalid topic' });

  const data = loadData();
  conversationMessages = [];

  try {
    const response = await getClient().messages.create({
      model: 'claude-sonnet-4-6',
      max_tokens: 200,
      messages: [{ role: 'user', content: buildStartPrompt(data, topicId) }]
    });

    const question = response.content[0].text.trim();
    conversationMessages = [{ role: 'assistant', content: question }];

    res.json({ question, stage: data.stage });
  } catch (e) {
    console.error('API error:', e.message);
    res.status(500).json({ error: 'AI request failed' });
  }
});

app.post('/api/chat', async (req, res) => {
  const { message, topicId } = req.body;
  if (!message) return res.status(400).json({ error: 'no message' });

  const data = loadData();
  conversationMessages.push({ role: 'user', content: message });

  try {
    const response = await getClient().messages.create({
      model: 'claude-sonnet-4-6',
      max_tokens: 500,
      system: buildSystemPrompt(data, topicId),
      messages: conversationMessages
    });

    const raw = response.content[0].text.trim();
    let parsed;
    try {
      const jsonMatch = raw.match(/\{[\s\S]*\}/);
      parsed = JSON.parse(jsonMatch ? jsonMatch[0] : raw);
    } catch (e) {
      parsed = {
        affirmation: '伝わりました！',
        corrections: [],
        phrases: [],
        point: '',
        followUp: raw
      };
    }

    conversationMessages.push({ role: 'assistant', content: raw });

    const wordCount = message.trim().split(/\s+/).filter(w => w.length > 0).length;
    const wasCorrected = parsed.corrections && parsed.corrections.length > 0;
    data.recentUtterances.push({ wordCount, wasCorrected });
    if (data.recentUtterances.length > 20) data.recentUtterances.shift();
    data.totalConversations++;
    const today = new Date().toISOString().slice(0, 10);
    if (!data.practiceDays.includes(today)) data.practiceDays.push(today);
    saveData(data);
    const stageUp = checkStageUp(data);

    res.json({ response: parsed, stageUp, stage: data.stage });
  } catch (e) {
    console.error('API error:', e.message);
    res.status(500).json({ error: 'AI request failed' });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Hiro Speaking Practice running on http://localhost:${PORT}`);
});
