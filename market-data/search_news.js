// 搜索 BlockBeats 快讯中的关键词
const fs = require('fs');
const kw = process.argv[2] || 'RATS';
const kwUpper = kw.toUpperCase();

const html = fs.readFileSync('/tmp/blockbeats2.html', 'utf-8');
const start = html.indexOf('window.__NUXT__=');
if (start === -1) { console.error('NUXT not found'); process.exit(1); }

const exprStart = start + 'window.__NUXT__='.length;
let depth = 0, end = exprStart, instr = false, esc = false;
for (let i = exprStart; i < html.length && i < exprStart + 500000; i++) {
    const c = html[i];
    if (esc) { esc = false; continue; }
    if (c === '\\') { esc = true; continue; }
    if (c === '"' || c === "'") { if (!instr) instr = c; else if (instr === c) instr = false; continue; }
    if (instr) continue;
    if ('({['.includes(c)) depth++;
    if (')}]'.includes(c)) { depth--; if (depth === 0) { end = i + 1; break; } }
}

const data = eval(html.substring(exprStart, end));

function findNews(obj, depth = 0) {
    if (!obj || typeof obj !== 'object' || depth > 10) return [];
    if (Array.isArray(obj)) return obj.flatMap(v => findNews(v, depth + 1));
    if (obj.title && obj.content) return [obj];
    return Object.values(obj).flatMap(v => findNews(v, depth + 1));
}

const news = findNews(data);
console.log(`共 ${news.length} 条快讯\n`);

let found = 0;
for (const item of news) {
    const text = (item.title + ' ' + (item.content || '')).toUpperCase();
    if (text.includes(kwUpper)) {
        found++;
        const clean = (item.content || '').replace(/<[^>]+>/g, '').replace(/https?:\/\/\S+/g, '').replace(/\s+/g, ' ').trim();
        console.log(`🔔 [${found}] ${item.title}`);
        console.log(`   ${clean.substring(0, 400)}`);
        console.log();
    }
}

if (found === 0) {
    console.log(`❌ 未找到 "${kw}" 相关快讯\n`);
    console.log('最近快讯标题:');
    news.slice(0, 20).forEach((item, i) => {
        console.log(`  ${i+1}. ${item.title.substring(0, 100)}`);
    });
}
