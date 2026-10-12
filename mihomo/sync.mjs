#!/usr/bin/env node
// 把 main 分支的四张 Shadowrocket 规则表转成 mihomo classical 文本，同步到 clash-ios 分支。
//
//   node mihomo/sync.mjs           # 转换 + 推送（有变化才提交，三张表合成一次提交）
//   node mihomo/sync.mjs --check   # 只报告是否落后，不写不改
//   node mihomo/sync.mjs --dry     # 转换并比对，但不推送
//
// 凭据：REPO_TOKEN 或 GITHUB_TOKEN（GitHub Action 里用后者）。
// 退出码：0 正常；1 出错或被护栏拦下。
//
// 转换只做两件事：去注释行、去第 3 列策略。类型与值逐字不动。
// 因此 DOMAIN-SUFFIX,x,REJECT-DROP → DOMAIN-SUFFIX,x；IP-CIDR,x,REJECT-DROP,no-resolve → IP-CIDR,x,no-resolve。
// 动作不写进文件，由引用方（Clash 配置的 RULE-SET 行）决定。

const OWNER = 'henrysha1989';
const REPO = 'shadowrocket-adr-rules';
const SRC_BRANCH = 'main';
const DST_BRANCH = 'clash-ios';
const DIR = 'mihomo';
const FILES = ['bytedance-ad.list', 'soul-ad.list', 'reject-custom.list', 'direct-custom.list'];

const checkOnly = process.argv.includes('--check');
const dryRun = process.argv.includes('--dry');
const TOKEN = process.env.REPO_TOKEN || process.env.GITHUB_TOKEN;

if (!TOKEN) {
  console.error('缺少 REPO_TOKEN / GITHUB_TOKEN 环境变量');
  process.exit(1);
}

async function api(method, path, body, accept = 'application/vnd.github+json') {
  const data = body ? JSON.stringify(body) : null;
  let last;
  for (let i = 1; i <= 6; i++) {
    try {
      const r = await fetch(`https://api.github.com${path}`, {
        method,
        headers: {
          'User-Agent': 'dsh-agent-sync',
          Authorization: `token ${TOKEN}`,
          Accept: accept,
          ...(data ? { 'Content-Type': 'application/json' } : {}),
        },
        body: data || undefined,
      });
      const text = await r.text();
      return { status: r.status, text };
    } catch (e) {
      last = e;
      await new Promise((s) => setTimeout(s, 1200 * i));
    }
  }
  throw last;
}

// 源表 / 目标文件都走 contents API 取原文，避开 CDN 缓存
async function readFile(path, ref) {
  const r = await api('GET', `/repos/${OWNER}/${REPO}/contents/${path}?ref=${ref}`, null, 'application/vnd.github.raw');
  if (r.status === 404) return null;
  if (r.status !== 200) throw new Error(`读 ${path}@${ref} 失败：HTTP ${r.status}`);
  return r.text;
}

function convert(text) {
  const out = [];
  for (const line of text.split(/\r?\n/)) {
    const l = line.trim();
    if (!l || l.startsWith('#')) continue;
    const p = l.split(',').map((x) => x.trim());
    if (p.length < 2) continue;
    const type = p[0].toUpperCase();
    out.push(type === 'IP-CIDR' ? `IP-CIDR,${p[1]},no-resolve` : `${type},${p[1]}`);
  }
  return out;
}

const countRules = (t) => (t ? t.split('\n').filter((l) => l.trim() && !l.startsWith('#')).length : 0);

// ---- 取源、转换、比对 ----
const srcRef = await api('GET', `/repos/${OWNER}/${REPO}/git/ref/heads/${SRC_BRANCH}`);
if (srcRef.status !== 200) throw new Error(`读 ${SRC_BRANCH} 失败：HTTP ${srcRef.status}`);
const srcSha = JSON.parse(srcRef.text).object.sha;

const rows = [];
for (const file of FILES) {
  const srcText = await readFile(file, SRC_BRANCH);
  if (srcText === null) throw new Error(`${SRC_BRANCH} 上找不到 ${file}`);

  const rules = convert(srcText);
  if (rules.length === 0) throw new Error(`⛔ ${file} 转换后是空的，中止（源表可能被清空或抓取异常）`);

  const curText = await readFile(`${DIR}/${file}`, DST_BRANCH);
  const curCount = countRules(curText);
  // 护栏：源表条数腰斩以上就停手，避免一次坏数据把线上规则集打空
  if (curCount > 0 && rules.length < curCount * 0.5) {
    throw new Error(`⛔ ${file} 条数从 ${curCount} 掉到 ${rules.length}（腰斩以上），中止`);
  }

  const next = rules.join('\n') + '\n';
  rows.push({ file, rules: rules.length, curCount, next, changed: next !== curText });
}

const changed = rows.filter((r) => r.changed);
const drift = changed.length > 0;

for (const r of rows) {
  const state = r.changed ? (r.curCount === 0 ? '分支缺文件' : `将更新 ${r.curCount}→${r.rules}`) : '已一致';
  console.log(`${r.file.padEnd(20)} 源 ${String(r.rules).padStart(3)} 条   分支 ${String(r.curCount).padStart(3)} 条   ${state}`);
}

if (checkOnly) {
  // 退出码给 CI 判定用：0=已同步，2=落后（workflow 里当失败处理，好触发告警）
  console.log(drift ? `\n落后：${changed.length} 个文件需要同步` : '\n已同步，无差异');
  process.exit(drift ? 2 : 0);
}
if (!drift) {
  console.log('\n无变化，不提交');
  process.exit(0);
}

// ---- 落一份本地工作副本（人工查看 / 排查用）----
const fs = await import('node:fs');
fs.mkdirSync(DIR, { recursive: true });
for (const r of rows) fs.writeFileSync(`${DIR}/${r.file}`, r.next);

if (dryRun) {
  console.log('\n--dry：不推送');
  process.exit(0);
}

// ---- 一次提交写完所有变动 ----
const dstRef = await api('GET', `/repos/${OWNER}/${REPO}/git/ref/heads/${DST_BRANCH}`);
if (dstRef.status !== 200) throw new Error(`读 ${DST_BRANCH} 失败：HTTP ${dstRef.status}`);
const headSha = JSON.parse(dstRef.text).object.sha;

const headCommit = await api('GET', `/repos/${OWNER}/${REPO}/git/commits/${headSha}`);
const baseTree = JSON.parse(headCommit.text).tree.sha;

const entries = [];
for (const r of changed) {
  const blob = await api('POST', `/repos/${OWNER}/${REPO}/git/blobs`, { content: r.next, encoding: 'utf-8' });
  if (blob.status !== 201) throw new Error(`建 blob 失败：HTTP ${blob.status} ${blob.text.slice(0, 200)}`);
  entries.push({ path: `${DIR}/${r.file}`, mode: '100644', type: 'blob', sha: JSON.parse(blob.text).sha });
}

const tree = await api('POST', `/repos/${OWNER}/${REPO}/git/trees`, { base_tree: baseTree, tree: entries });
if (tree.status !== 201) throw new Error(`建 tree 失败：HTTP ${tree.status} ${tree.text.slice(0, 200)}`);

const message = `chore(mihomo): sync from ${SRC_BRANCH}@${srcSha.slice(0, 7)}\n\n${changed
  .map((r) => `${r.file}: ${r.curCount} → ${r.rules}`)
  .join('\n')}`;

const commit = await api('POST', `/repos/${OWNER}/${REPO}/git/commits`, {
  message,
  tree: JSON.parse(tree.text).sha,
  parents: [headSha],
});
if (commit.status !== 201) throw new Error(`建 commit 失败：HTTP ${commit.status} ${commit.text.slice(0, 200)}`);

const moved = await api('PATCH', `/repos/${OWNER}/${REPO}/git/refs/heads/${DST_BRANCH}`, {
  sha: JSON.parse(commit.text).sha,
});
if (moved.status !== 200) throw new Error(`更新 ${DST_BRANCH} 失败：HTTP ${moved.status} ${moved.text.slice(0, 200)}`);

console.log(`\n已同步 ${changed.length} 个文件 → ${DST_BRANCH}，commit=${JSON.parse(commit.text).sha.slice(0, 7)}（源 ${SRC_BRANCH}@${srcSha.slice(0, 7)}）`);
