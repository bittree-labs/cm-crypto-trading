/**
 * BlockBeats 快讯 → Binance USDT 永续合约 匹配器
 * 用法: node news_match.js
 */

const fs = require('fs');
const https = require('https');

// ─── 1. 读取 HTML 并解析 NUXT 数据 ───────────────────────────

const html = fs.readFileSync('/tmp/blockbeats.html', 'utf-8');

// 用括号深度追踪提取完整的 NUXT 表达式
const nuxtStart = html.indexOf('window.__NUXT__=');
if (nuxtStart === -1) {
    console.error('❌ 未找到 NUXT 数据');
    process.exit(1);
}
const exprStart = nuxtStart + 'window.__NUXT__='.length;
let depth = 0;
let end = exprStart;
let inString = false;
let esc = false;
for (let i = exprStart; i < html.length && i < exprStart + 500000; i++) {
    const ch = html[i];
    if (esc) { esc = false; continue; }
    if (ch === '\\') { esc = true; continue; }
    if (ch === '"' || ch === "'") {
        if (!inString) inString = ch;
        else if (inString === ch) inString = false;
        continue;
    }
    if (inString) continue;
    if (ch === '(' || ch === '{' || ch === '[') depth++;
    if (ch === ')' || ch === '}' || ch === ']') { depth--; if (depth === 0) { end = i + 1; break; } }
}

const nuxtExpr = html.substring(exprStart, end);
console.log(`📄 NUXT 表达式长度: ${nuxtExpr.length} 字符, 结尾: ...${html.substring(end-10,end+10)}`)

// eval NUXT 数据
let nuxtData;
try {
    nuxtData = eval(nuxtExpr);
} catch (e) {
    console.error('❌ NUXT eval 失败:', e.message);
    // 尝试附加括号
    try {
        nuxtData = eval('(' + nuxtExpr + ')');
    } catch (e2) {
        console.error('❌ 二次尝试也失败:', e2.message);
        process.exit(1);
    }
}

// ─── 2. 提取快讯列表 ────────────────────────────────────────

const newsItems = [];

function extractNews(obj, depth = 0) {
    if (!obj || typeof obj !== 'object' || depth > 10) return;
    
    if (Array.isArray(obj)) {
        // 检查是否是 days 数组
        for (const item of obj) {
            if (item && item.children && Array.isArray(item.children)) {
                for (const child of item.children) {
                    if (child && child.title) {
                        newsItems.push({
                            title: child.title,
                            content: child.content || child.abstract || '',
                            url: child.url || '',
                            time: child.add_time || '',
                            cryptoToken: child.crypto_token || '',
                        });
                    }
                }
            }
            extractNews(item, depth + 1);
        }
    } else {
        for (const key of Object.keys(obj)) {
            extractNews(obj[key], depth + 1);
        }
    }
}

extractNews(nuxtData);
console.log(`📰 提取到 ${newsItems.length} 条快讯`);

if (newsItems.length === 0) {
    // 备用：直接搜索已知模式
    console.log('⚠️  结构遍历未找到快讯，尝试备用解析...');
    // 遍历 nuxtData 看看实际结构
    console.log('顶层 keys:', Object.keys(nuxtData));
    if (nuxtData.data) {
        console.log('data 类型:', Array.isArray(nuxtData.data), '长度:', nuxtData.data?.length);
        if (nuxtData.data?.[0]) {
            console.log('data[0] keys:', Object.keys(nuxtData.data[0]));
            if (nuxtData.data[0]?.days) {
                console.log('days 长度:', nuxtData.data[0].days.length);
                const d = nuxtData.data[0].days[0];
                console.log('day keys:', Object.keys(d));
                if (d.children) {
                    console.log('children 长度:', d.children.length);
                    if (d.children[0]) {
                        console.log('child[0]:', JSON.stringify(d.children[0]).substring(0, 300));
                    }
                }
            }
        }
    }
}

// ─── 3. 打印快讯摘要 ─────────────────────────────────────────

console.log('\n' + '═'.repeat(70));
console.log('📋 今日快讯列表');
console.log('═'.repeat(70));

newsItems.slice(0, 30).forEach((item, i) => {
    const time = item.time ? new Date(item.time * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '--:--';
    console.log(`  ${String(i + 1).padStart(2)}. [${time}] ${item.title.substring(0, 80)}`);
});

if (newsItems.length > 30) {
    console.log(`  ... 还有 ${newsItems.length - 30} 条`);
}

// ─── 4. 获取 Binance USDT 永续合约列表 ──────────────────────

console.log('\n' + '═'.repeat(70));
console.log('🔍 获取 Binance USDT 永续合约列表...');
console.log('═'.repeat(70));

function fetchJSON(url) {
    return new Promise((resolve, reject) => {
        https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' } }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); }
                catch (e) { reject(e); }
            });
        }).on('error', reject);
    });
}

async function main() {
    let binancePairs = [];
    try {
        const exchangeInfo = await fetchJSON('https://fapi.binance.com/fapi/v1/exchangeInfo');
        for (const s of exchangeInfo.symbols) {
            if (s.quoteAsset === 'USDT' && s.contractType === 'PERPETUAL' && s.status === 'TRADING') {
                const base = s.symbol.replace('USDT', '');
                binancePairs.push({ symbol: s.symbol, base });
            }
        }
        console.log(`✅ 获取到 ${binancePairs.length} 个 USDT 永续合约交易对`);
    } catch (e) {
        console.error('❌ 获取 Binance 合约列表失败:', e.message);
        // 使用硬编码的常见列表作为后备
        console.log('⚠️  使用本地缓存列表');
        binancePairs = getFallbackPairs();
    }

    // 构建 base 查找集合（用 Set 快速匹配）
    const pairSet = new Set(binancePairs.map(p => p.base.toUpperCase()));
    const pairMap = {};
    for (const p of binancePairs) {
        pairMap[p.base.toUpperCase()] = p.symbol;
    }

    // ─── 5. 构建代币名称映射 ──────────────────────────────────

    // 代币名称 → 符号 映射（处理中文名、英文全名等）
    const tokenAliases = buildTokenAliases(pairSet);

    console.log(`🔗 构建了 ${Object.keys(tokenAliases).length} 个代币别名映射`);

    // ─── 6. 匹配快讯中的代币 ──────────────────────────────────

    console.log('\n' + '═'.repeat(70));
    console.log('🎯 命中结果：快讯中提到且有 Binance USDT 永续合约的项目');
    console.log('═'.repeat(70));

    let hitCount = 0;
    const matches = [];

    // 常见英文单词黑名单（短符号容易误匹配成普通单词）
    const blacklistWords = new Set([
        'A', 'I', 'ME', 'BE', 'GO', 'DO', 'SO', 'IT', 'AT', 'IN', 'ON', 'AN', 'IS', 'AM', 'WE', 'HE', 'NO', 'OR',
        'AI', 'HI', 'BY', 'MY', 'TO', 'IF', 'OF', 'AS', 'US', 'UP',
        'BAT', 'CAR', 'TOP', 'SUN', 'KEY', 'NEW', 'ICE', 'HOT', 'BOX',
        'ACE', 'ACT', 'ALT', 'ARC', 'BIO', 'COW', 'DEEP', 'DOGS', 'EGP', 'IP',
        'MOVE', 'NOT', 'OMNI', 'POPCAT', 'SAFE', 'TURBO', 'USUAL', 'W',
        'ME', 'GRASS', 'GOAT', 'MAJOR', 'COOKIE', 'BANANA', 'HAMSTER',
    ]);

    for (const item of newsItems) {
        // 清洗文本：去 HTML 标签、去 URL、保留纯文本
        let rawText = item.title + ' ' + (item.content || '');
        rawText = rawText.replace(/<[^>]+>/g, ' ');
        rawText = rawText.replace(/https?:\/\/\S+/g, ' ');
        rawText = rawText.replace(/[&<>"']/g, ' ');
        rawText = rawText.replace(/\s+/g, ' ').trim();

        const textUpper = rawText.toUpperCase();
        const foundTokens = new Set();

        // 直接符号匹配：仅对长度 >= 3 的 base 使用 \b 边界匹配
        for (const base of pairSet) {
            if (blacklistWords.has(base)) continue;
            if (base.length < 2) continue;  // 跳过单字母（几乎没有 USDT 合约）
            const escBase = base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

            if (base.length >= 3) {
                // 长符号：标准词边界匹配
                const regex = new RegExp('\\b' + escBase + '\\b');
                if (regex.test(textUpper)) {
                    foundTokens.add(base);
                }
            }
            // 双字母符号跳过直接匹配，仅通过别名匹配
            // 因为双字母太容易误匹配（如 AI, OP, ME, IP 等）
        }

        // 别名匹配（中文名、英文全名等）
        for (const [alias, base] of Object.entries(tokenAliases)) {
            const aliasUpper = alias.toUpperCase();
            const hasCJK = /[\u4e00-\u9fff]/.test(alias);
            if (hasCJK || alias.length >= 4) {
                // 中文别名或长别名：直接用子串匹配（中文无词边界概念，且误匹配概率极低）
                if (textUpper.includes(aliasUpper)) foundTokens.add(base);
            } else {
                // 短英文别名（<=3字符）：要求词边界，避免子串误匹配
                const esc = aliasUpper.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                if (new RegExp('\\b' + esc + '\\b').test(textUpper)) foundTokens.add(base);
            }
        }

        if (foundTokens.size > 0) {
        }

        if (foundTokens.size > 0) {
            hitCount++;
            const time = item.time ? new Date(item.time * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '--:--';
            const tokens = [...foundTokens].map(t => pairMap[t] || t + 'USDT').join(', ');

            matches.push({ time, title: item.title, tokens: [...foundTokens] });

            console.log(`\n  🔔 [${time}] ${tokens}`);
            console.log(`     ${item.title.substring(0, 120)}`);
            if (item.content) {
                console.log(`     ${item.content.substring(0, 120)}`);
            }
        }
    }

    if (hitCount === 0) {
        console.log('  ℹ️  当前快讯中未发现匹配的 Binance USDT 永续合约项目');
    }

    console.log('\n' + '═'.repeat(70));
    console.log(`📊 统计: ${newsItems.length} 条快讯, ${hitCount} 条命中合约项目`);
    console.log('═'.repeat(70));

    // ─── 7. 检查快讯中是否自带 crypto_token ──────────────────

    const withToken = newsItems.filter(n => n.cryptoToken);
    if (withToken.length > 0) {
        console.log(`\n📌 ${withToken.length} 条快讯自带 crypto_token 标签:`);
        for (const item of withToken) {
            const inBinance = pairSet.has(item.cryptoToken.toUpperCase()) ? '✅' : '❌';
            console.log(`  ${inBinance} [${item.cryptoToken}] ${item.title.substring(0, 80)}`);
        }
    }
}

// ─── 别名映射构建 ────────────────────────────────────────────

function buildTokenAliases(pairSet) {
    const aliases = {};

    // 已知的代币全名/中文名映射（binance 上的热门合约）
    const knownNames = {
        'BITCOIN': 'BTC', '比特币': 'BTC', '大饼': 'BTC',
        'ETHEREUM': 'ETH', '以太坊': 'ETH', '以太': 'ETH',
        'SOLANA': 'SOL', '索拉纳': 'SOL',
        'RIPPLE': 'XRP', '瑞波': 'XRP',
        'CARDANO': 'ADA', '艾达': 'ADA',
        'AVALANCHE': 'AVAX', '雪崩': 'AVAX',
        'POLKADOT': 'DOT', '波卡': 'DOT',
        'DOGECOIN': 'DOGE', '狗狗币': 'DOGE', '狗币': 'DOGE',
        'CHAINLINK': 'LINK',
        'UNISWAP': 'UNI',
        'LITECOIN': 'LTC', '莱特币': 'LTC', '莱特': 'LTC',
        'POLYGON': 'POL', '马蹄': 'POL', 'MATIC': 'POL',
        'SHIBA INU': 'SHIB', 'SHIB': 'SHIB', '柴犬币': 'SHIB',
        'TRON': 'TRX', '波场': 'TRX',
        'TONCOIN': 'TON', 'TON': 'TON',
        'APTOS': 'APT',
        'ARBITRUM': 'ARB',
        'OPTIMISM': 'OP',
        'SUI': 'SUI',
        'SEI': 'SEI',
        'NEAR PROTOCOL': 'NEAR', 'NEAR': 'NEAR',
        'COSMOS': 'ATOM', '阿童木': 'ATOM',
        'FILECOIN': 'FIL', 'FIL': 'FIL',
        'PEPE': 'PEPE',
        'BONK': 'BONK',
        'FLOKI': 'FLOKI',
        'WORLDCOIN': 'WLD', 'WLD': 'WLD',
        'INJECTIVE': 'INJ',
        'RENDER': 'RNDR', 'RNDR': 'RNDR',
        'STACKS': 'STX',
        'IMMUTABLE': 'IMX', 'IMX': 'IMX',
        'MAKER': 'MKR',
        'AAVE': 'AAVE',
        'FETCH': 'FET', 'FET': 'FET',
        'AGIX': 'AGIX',
        'OCEAN': 'OCEAN',
        'ORDI': 'ORDI',
        '1000SATS': 'SATS', 'SATS': '1000SATS',
        'JUPITER': 'JUP', 'JUP': 'JUP',
        'PYTH': 'PYTH',
        'WIF': 'WIF',
        'JTO': 'JTO',
        'TIA': 'TIA',
        'DYM': 'DYM',
        'STRK': 'STRK',
        'ENA': 'ENA',
        'ETHENA': 'ENA',
        'WORMHOLE': 'W',
        'EIGENLAYER': 'EIGEN', 'EIGEN': 'EIGEN',
        'ZK': 'ZK',
        'ZKSYNC': 'ZK',
        'LAYERZERO': 'ZRO', 'ZRO': 'ZRO',
        'HYPERLIQUID': 'HYPE', 'HYPE': 'HYPE',
        'VIRTUAL': 'VIRTUAL',
        'GRASS': 'GRASS',
        'MOVE': 'MOVE',
        'BERA': 'BERA',
        'BERACHAIN': 'BERA',
        'IP': 'IP',
        'KAITO': 'KAITO',
        'MELANIA': 'MELANIA',
        'TRUMP': 'TRUMP',
        'PENGU': 'PENGU',
        'ANIME': 'ANIME',
        'BIO': 'BIO',
        'VANA': 'VANA',
        'ME': 'ME',
        'MORPHO': 'MORPHO',
        'ACX': 'ACX',
        'USUAL': 'USUAL',
        'MOCA': 'MOCA',
        'COW': 'COW',
        'CETUS': 'CETUS',
        'VELODROME': 'VELODROME',
        'AERODROME': 'AERO', 'AERO': 'AERO',
        'PENDLE': 'PENDLE',
        'EIGENPIE': 'EGP',
        'LISTA': 'LISTA',
        'LISTA DAO': 'LISTA',
        'ZRO': 'ZRO',
        'NOTCOIN': 'NOT', 'NOT': 'NOT',
        'DOGS': 'DOGS',
        'HMSTR': 'HMSTR', 'HAMSTER': 'HMSTR',
        'CATI': 'CATI',
        'MAJOR': 'MAJOR',
        'XEMP': 'XEMP',
        'MEMEFI': 'MEMEFI',
        'TURBO': 'TURBO',
        'BRETT': 'BRETT',
        'POPCAT': 'POPCAT',
        'GOAT': 'GOAT',
        'MOODENG': 'MOODENG',
        'PNUT': 'PNUT',
        'ACT': 'ACT',
        'NEIRO': 'NEIRO',
        'FWOG': 'FWOG',
        'AI16Z': 'AI16Z',
        'AIXBT': 'AIXBT',
        'ARC': 'ARC',
        'COOKIE': 'COOKIE',
        'FARTCOIN': 'FARTCOIN',
        'SWARMS': 'SWARMS',
        'GRIFFAIN': 'GRIFFAIN',
        'AVAAI': 'AVAAI',
        'PAAL': 'PAAL',
        'OLAS': 'OLAS',
        'AI_RIG': 'AIC',
        'SAFE': 'SAFE',
        'DEEP': 'DEEP',
        'SCR': 'SCR',
        'KAIA': 'KAIA',
        'LUMIA': 'LUMIA',
        'EIGENPIE': 'EGP',
        'MAGICEDEN': 'ME',
        'ME': 'ME',
        // 'BANANA' 移除：英文常见词汇，误匹配率高（如 Google Nano Banana）
        'ZKL': 'ZKL',
        'ZETA': 'ZETA',
        'OMNI': 'OMNI',
        'REZ': 'REZ',
        'ETHFI': 'ETHFI',
        'ALTLAYER': 'ALT',
        'PORTAL': 'PORTAL',
        'PIXEL': 'PIXEL',
        'XAI': 'XAI',
        'ACE': 'ACE',
        'NFP': 'NFP',
        'AI': 'AI',
        'BOME': 'BOME',
        'SLERF': 'SLERF',
        'MYRO': 'MYRO',
        'WEN': 'WEN',
    };

    for (const [alias, base] of Object.entries(knownNames)) {
        if (pairSet.has(base.toUpperCase())) {
            aliases[alias.toUpperCase()] = base.toUpperCase();
        }
    }

    return aliases;
}

function getFallbackPairs() {
    // 后备列表：常见 USDT 永续合约
    return [
        'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'AVAX', 'DOT', 'LINK',
        'LTC', 'UNI', 'MATIC', 'SHIB', 'TRX', 'TON', 'APT', 'ARB', 'OP', 'SUI',
        'SEI', 'NEAR', 'ATOM', 'FIL', 'PEPE', 'BONK', 'FLOKI', 'WLD', 'INJ',
        'RNDR', 'STX', 'IMX', 'MKR', 'AAVE', 'FET', 'ORDI', '1000SATS', 'JUP',
        'PYTH', 'WIF', 'JTO', 'TIA', 'DYM', 'STRK', 'ENA', 'W', 'EIGEN', 'ZK',
        'ZRO', 'HYPE', 'VIRTUAL', 'GRASS', 'MOVE', 'BERA', 'IP', 'KAITO',
        'MELANIA', 'TRUMP', 'PENGU', 'ANIME', 'BIO', 'VANA', 'MORPHO', 'ACX',
        'USUAL', 'MOCA', 'COW', 'CETUS', 'AERO', 'PENDLE', 'NOT', 'DOGS',
        'HMSTR', 'CATI', 'TURBO', 'POPCAT', 'GOAT', 'MOODENG', 'PNUT', 'ACT',
        'NEIRO', 'AI16Z', 'AIXBT', 'ARC', 'COOKIE', 'FARTCOIN', 'SWARMS',
        'GRIFFAIN', 'AVAAI', 'PAAL', 'AIC', 'SAFE', 'DEEP', 'SCR', 'KAIA',
        'LUMIA', 'EGP', 'ME', 'BANANA', 'ZKL', 'ZETA', 'OMNI', 'REZ', 'ETHFI',
        'ALT', 'PORTAL', 'PIXEL', 'XAI', 'ACE', 'NFP', 'AI', 'BOME', 'SLERF',
    ].map(b => ({ symbol: b + 'USDT', base: b }));
}

main().catch(e => {
    console.error('❌ 运行错误:', e);
    process.exit(1);
});
