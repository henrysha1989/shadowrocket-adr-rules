#!/usr/bin/env node
// 校验 module/*.module：正则能编译 + 每条能命中自己的"实体化样本" + 负样本不命中 +
// 结构红线（不许有 [Rule]/IP-CIDR/DOMAIN-KEYWORD/第三方 JS）+ MITM 域名与正则互相覆盖。
//   node ops/shadowrocket/module/validate-module.mjs ops/shadowrocket/module/bytedance-ad.module
import fs from 'node:fs';

const file = process.argv[2];
if (!file) { console.error('usage: validate-module.mjs <file.module>'); process.exit(2); }
const text = fs.readFileSync(file, 'utf8');

// ---- 解析 ----
const lines = text.split(/\r?\n/);
const meta = {};
const sections = new Map();
let cur = '(meta)';
sections.set(cur, []);
for (const raw of lines) {
  const L = raw.trim();
  const s = L.match(/^\[([^\]]+)\]\s*$/);
  if (s) { cur = s[1].trim(); if (!sections.has(cur)) sections.set(cur, []); continue; }
  if (L.startsWith('#!')) { const i = L.indexOf('='); meta[L.slice(2, i)] = L.slice(i + 1); continue; }
  sections.get(cur).push(L);
}

const problems = [];
const rewrites = [];
for (const L of sections.get('URL Rewrite') || []) {
  if (!L || L.startsWith('#') || L.startsWith(';')) continue;
  const m = L.match(/^(.*?)\s+-\s+(\S+)\s*$/);
  if (!m) { problems.push(`URL Rewrite 行缺 " - 动作"：${L.slice(0, 60)}`); continue; }
  rewrites.push({ pattern: m[1], action: m[2], line: L });
}

// ---- 红线：结构 ----
const badActions = new Set();
for (const r of rewrites) {
  if (!/^(reject|reject-200|reject-img|reject-dict|reject-array|302|307)$/.test(r.action)) badActions.add(r.action);
  if (/ip-cidr|domain-keyword|domain-suffix|[,]direct$|[,]proxy$/i.test(r.pattern)) problems.push(`正则里出现分流语法：${r.pattern.slice(0, 50)}`);
}
if (badActions.size) problems.push(`未知/不放心的动作：${[...badActions].join(', ')}`);
for (const sec of sections.keys()) {
  const body = sections.get(sec).filter(Boolean);
  if (!['(meta)', 'URL Rewrite', 'MITM'].includes(sec)) problems.push(`出现了不该有的段：${sec}（${body.length} 行）`);
}
for (const L of sections.get('Rule') || []) if (L && !L.startsWith('#')) problems.push(`[Rule] 段非空：${L.slice(0, 50)}`);
if (/IP-CIDR|DOMAIN-KEYWORD/i.test(text)) problems.push('文本里出现 IP-CIDR / DOMAIN-KEYWORD');
if (/^\s*[A-Za-z0-9_-]+\s*=\s*type=/m.test(text)) problems.push('文本里出现 [Script] 型脚本行（模块不允许跑第三方 JS）');

// ---- 正则编译 + 自命中 ----
// 逐字符实体化：把正则还原成一条"应该被自己命中"的样例 URL。
// 注意 `.` `+` `?` 三种含义要分开处理：未转义的 `?` 是量词/普通字符（https?），`\?` 是字面问号。
function materialize(src) {
  let out = '';
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c === '\\') { const n = src[++i]; out += n === undefined ? '\\' : n; continue; }
    if (c === '^' || c === '$') continue;
    if (c === '.' && src[i + 1] === '+') {          // .+ / .+?
      i++;
      if (src[i + 1] === '?') i++;
      out += 'x';
      continue;
    }
    if (c === '?') continue;                         // https? → https
    if (c === '(') {                                 // (a|b) → a
      let depth = 1, buf = '', j = i + 1;
      for (; j < src.length && depth > 0; j++) {
        const d = src[j];
        if (d === '\\') { buf += src[j + 1] ?? ''; j++; continue; }
        if (d === '(') depth++;
        if (d === ')') { depth--; if (!depth) break; }
        buf += d;
      }
      i = j;
      out += buf.split('|')[0];
      continue;
    }
    out += c;
  }
  return out.replace(/^https:\/\//, 'https://');
}
for (const [i, r] of rewrites.entries()) {
  let re;
  try { re = new RegExp(r.pattern); } catch (e) { problems.push(`第 ${i + 1} 条正则编译失败：${r.pattern.slice(0, 50)} → ${e.message}`); continue; }
  const sample = materialize(r.pattern);
  if (!/^https?:\/\//.test(sample)) { problems.push(`第 ${i + 1} 条样本不成立：${sample}`); continue; }
  if (!re.test(sample)) problems.push(`第 ${i + 1} 条命中不了自己的样本 ${sample}`);
  r.sample = sample;
}

// ---- 负样本（内容侧，必须不命中）----
const negatives = [
  'https://p3-reading-video.fqnovelpic.com/reading-video/abc.mp4',
  'https://p9-reading-video.qznovelvod.com/reading/video/abc/index.m3u8',
  'https://p6.pstatp.com/obj/reading-video/abc.mp4',
  'https://p3.byteimg.com/tos-cn-i-1yzifmftcy/content-image.jpeg',
  'https://i.snssdk.com/api/feed/content/',
  'https://gurd.snssdk.com/src/server/v3/config',
];
for (const [i, r] of rewrites.entries()) {
  const re = new RegExp(r.pattern);
  for (const n of negatives) if (re.test(n)) problems.push(`第 ${i + 1} 条误伤负样本：${n}`);
}

// ---- MITM 域名覆盖 ----
const mitm = [];
for (const L of sections.get('MITM') || []) {
  if (!L || L.startsWith('#')) continue;
  const m = L.match(/^hostname\s*=\s*(.*)$/i);
  if (!m) { problems.push(`[MITM] 段出现不认识的行：${L.slice(0, 50)}`); continue; }
  m[1].replace(/%APPEND%/i, '').split(',').forEach((h) => { h = h.trim(); if (h) mitm.push(h); });
}
const bare = mitm.filter((h) => !h.startsWith('*.') && !h.startsWith('-'));
for (const h of mitm) {
  const core = h.replace(/^\*\.?/, '').toLowerCase();
  const used = rewrites.some((r) => r.pattern.toLowerCase().replace(/\\/g, '').includes(core));
  if (!used) problems.push(`MITM 域名 ${h} 没有任何正则用到（多余的解密面）`);
}
console.log(`文件：${file}`);
console.log(`模块名：${meta.name || '(缺 #!name)'}  作者：${meta.author || '-'}`);
console.log(`段：${[...sections.keys()].join(' / ')}`);
console.log(`URL Rewrite：${rewrites.length} 条，动作：${JSON.stringify(rewrites.reduce((a, r) => (a[r.action] = (a[r.action] || 0) + 1, a), {}))}`);
console.log(`MITM 域名：${mitm.length} 个（裸域 ${bare.length} 个）`);
console.log(`负样本 ${negatives.length} 条，全部通过（没有误伤）`);
console.log(problems.length ? '\n❌ 问题：\n' + problems.map((p) => '  - ' + p).join('\n') : '\n✅ 校验通过');
process.exit(problems.length ? 1 : 0);
