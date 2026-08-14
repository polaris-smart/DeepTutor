# A4 打印 CSS 骨架

生成独立 HTML 时，把下面整个代码块放入 `<style>`。默认每课内部是“核心/易错”双栏；如果内容很多，需要把多课压成页面双栏，在 `.sheet` 上追加 `page-two-column` 类。不要缩放页面或把正文调到 11pt 以下。

```css
@page {
  size: A4 portrait;
  margin: 11mm;
}

:root {
  --ink: #172033;
  --muted: #536174;
  --line: #cfd7e3;
  --paper: #ffffff;
  --core-bg: #eef7ff;
  --core-line: #4b91d1;
  --mistake-bg: #fff3ef;
  --mistake-line: #dd6b55;
}

* {
  box-sizing: border-box;
}

html {
  background: #eef1f5;
  color: var(--ink);
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

body {
  margin: 0;
  font-family: "Noto Sans SC", "Source Han Sans SC", "PingFang SC",
    "Microsoft YaHei", "Apple Color Emoji", "Segoe UI Emoji",
    "Noto Color Emoji", sans-serif;
  font-size: 11pt;
  line-height: 1.5;
}

.sheet {
  width: min(188mm, calc(100% - 24px));
  min-height: 275mm;
  margin: 20px auto;
  padding: 10mm;
  background: var(--paper);
  box-shadow: 0 8px 30px rgb(23 32 51 / 12%);
}

.handoff-comment {
  display: none;
}

.title {
  margin: 0 0 3mm;
  font-size: 20pt;
  line-height: 1.25;
  text-align: center;
}

.lead {
  margin: 0 0 4mm;
  padding: 3mm 4mm;
  border: 1px solid var(--line);
  border-radius: 2mm;
  background: #f8fafc;
}

.lead p {
  margin: 1mm 0;
}

.lesson-list {
  margin: 0;
  padding: 0;
}

.lesson {
  margin: 0 0 4mm;
  break-inside: avoid;
  page-break-inside: avoid;
}

.lesson > h2 {
  margin: 0 0 2mm;
  padding-bottom: 1.5mm;
  border-bottom: 1.5px solid var(--ink);
  font-size: 14pt;
  line-height: 1.3;
}

.lesson-grid {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 3mm;
  align-items: start;
}

.panel {
  min-width: 0;
  padding: 3mm;
  border: 1px solid var(--line);
  border-left-width: 3px;
  border-radius: 2mm;
  break-inside: avoid;
  page-break-inside: avoid;
}

.core-panel {
  border-left-color: var(--core-line);
  background: var(--core-bg);
}

.mistake-panel {
  border-left-color: var(--mistake-line);
  background: var(--mistake-bg);
}

.panel h3 {
  margin: 0 0 2mm;
  font-size: 12pt;
  line-height: 1.35;
}

.panel ol,
.panel ul {
  margin: 0;
  padding-left: 6mm;
}

.panel li {
  margin: 0 0 1.5mm;
  orphans: 2;
  widows: 2;
}

.panel li:last-child {
  margin-bottom: 0;
}

.mistake-panel ul {
  list-style: none;
  padding-left: 0;
}

.unit-frame {
  margin-top: 4mm;
  padding: 3mm 4mm;
  border: 1px dashed var(--core-line);
  border-radius: 2mm;
  break-inside: avoid;
  page-break-inside: avoid;
}

.unit-frame h2 {
  margin: 0 0 2mm;
  font-size: 14pt;
}

.unit-frame p,
.unit-frame li {
  margin: 1mm 0;
}

.handoff-hook {
  margin: 5mm 0 0;
  padding-top: 3mm;
  border-top: 1px solid var(--line);
  font-weight: 700;
  text-align: center;
}

/* 可选：多课页面双栏。每张课卡内部改为上下排列，避免形成四个窄栏。 */
.page-two-column .lesson-list {
  column-count: 2;
  column-gap: 6mm;
  column-rule: 1px solid var(--line);
}

.page-two-column .lesson {
  display: inline-block;
  width: 100%;
}

.page-two-column .lesson-grid {
  display: block;
}

.page-two-column .mistake-panel {
  margin-top: 2mm;
}

@media screen and (max-width: 760px) {
  .sheet {
    width: calc(100% - 16px);
    min-height: 0;
    margin: 8px auto;
    padding: 16px;
  }

  .lesson-grid {
    grid-template-columns: 1fr;
  }

  .page-two-column .lesson-list {
    column-count: 1;
  }
}

@media print {
  html,
  body {
    background: #fff;
  }

  body {
    font-size: 11pt;
  }

  .sheet {
    width: auto;
    min-height: 0;
    margin: 0;
    padding: 0;
    box-shadow: none;
  }

  a {
    color: inherit;
    text-decoration: none;
  }
}
```

## HTML 结构映射

```html
<!-- deeptutor_handoff: ...完整字段... -->
<main class="sheet">
  <h1 class="title">📝 《教材名》《单元名》考前抢分清单</h1>
  <section class="lead">...</section>
  <div class="lesson-list">
    <article class="lesson">
      <h2>第 N 课 课名</h2>
      <div class="lesson-grid">
        <section class="panel core-panel"><h3>🎯 必背核心考点</h3><ol>...</ol></section>
        <section class="panel mistake-panel"><h3>⚠️ 易错易混点</h3><ul>...</ul></section>
      </div>
    </article>
  </div>
  <section class="unit-frame">...</section>
  <p class="handoff-hook">要配套的同范围练习 → 说“来一组”</p>
</main>
```

输出前确认 HTML 含 `<!doctype html>`、`<meta charset="utf-8">`、视口元数据、完整内嵌 CSS 和标准 header 注释块；不要引用外部字体、样式表或脚本。
