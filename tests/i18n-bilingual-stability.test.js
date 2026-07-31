/**
 * AIPOS-288 FIX-3d: i18n bilingual stability verification
 * 
 * Static verification + documented manual smoke test steps
 * (Playwright browser install fails on ubuntu26.04; this is "最强形式" achievable)
 */

const fs = require('fs');
const path = require('path');

console.log('📋 AIPOS-288 FIX-3d: i18n Bilingual Stability Verification\n');

// 1. Verify getCurrentLang() fixes are in place
console.log('1️⃣  Verifying getCurrentLang() API fixes...');
const projectDetailPath = path.join(__dirname, '../web/board/static/project-detail.html');
const content = fs.readFileSync(projectDetailPath, 'utf8');

const badCalls = content.match(/i18n\.currentLang\s*\(/g);
if (badCalls && badCalls.length > 0) {
  console.error(`   ❌ FAIL: Found ${badCalls.length} i18n.currentLang() calls (should be getCurrentLang())`);
  process.exit(1);
}

const goodCalls = content.match(/i18n\.getCurrentLang\s*\(/g);
console.log(`   ✅ PASS: All calls use i18n.getCurrentLang() (${goodCalls ? goodCalls.length : 0} instances)`);

// 2. Verify placeholder i18n keys exist
console.log('\n2️⃣  Verifying placeholder i18n keys exist...');
const i18nPath = path.join(__dirname, '../web/board/static/i18n.js');
const i18nContent = fs.readFileSync(i18nPath, 'utf8');

const requiredKeys = [
  'detail.loading',
  'tc.loading',
  'onboarding.loading'
];

let keysMissing = false;
for (const key of requiredKeys) {
  const zhMatch = i18nContent.match(new RegExp(`'${key}':\\s*'[^']*'`));
  const enMatch = i18nContent.match(new RegExp(`'${key}':\\s*'[^']*'`, 'g'));
  
  if (!zhMatch || (enMatch && enMatch.length < 2)) {
    console.error(`   ❌ FAIL: Key '${key}' missing in zh or en dictionary`);
    keysMissing = true;
  }
}

if (keysMissing) {
  process.exit(1);
}
console.log(`   ✅ PASS: All ${requiredKeys.length} placeholder keys exist in both zh/en`);

// 3. Verify placeholders use zh fallback text (matching default lang)
console.log('\n3️⃣  Verifying placeholders use zh fallback text...');
const placeholders = [
  { pattern: /data-i18n="detail\.loading">([^<]+)</, expected: '加载项目详情...' },
  { pattern: /data-i18n="tc\.loading">([^<]+)</, expected: '加载中...' },
  { pattern: /data-i18n="onboarding\.loading">([^<]+)</, expected: '加载中...' }
];

let wrongFallback = false;
for (const { pattern, expected } of placeholders) {
  const matches = [...content.matchAll(new RegExp(pattern.source, 'g'))];
  for (const match of matches) {
    if (match[1] !== expected) {
      console.error(`   ❌ FAIL: Placeholder has wrong fallback: "${match[1]}" (expected "${expected}")`);
      wrongFallback = true;
    }
  }
}

if (wrongFallback) {
  process.exit(1);
}
console.log('   ✅ PASS: All placeholders use zh fallback text (matching default lang)');

// 4. Document manual smoke test (since Playwright can't run on ubuntu26.04)
console.log('\n4️⃣  Manual Smoke Test Steps (Playwright unavailable on ubuntu26.04):');
console.log('   ⚠️  Run the following manual verification:\n');
console.log('   a) Start server: cd ~/projects/lybra && python3 -m web.board.app');
console.log('   b) Open browser: http://127.0.0.1:7117/project/lybra');
console.log('   c) ZH mode: Check browser console for ReferenceError/TypeError');
console.log('              Verify loading placeholders show "加载项目详情..." etc.');
console.log('   d) EN mode: Switch language via UI or localStorage.setItem("lybra_lang", "en")');
console.log('              Reload page, check console for errors');
console.log('              Verify loading placeholders show "Loading project detail..." etc.');
console.log('   e) Assert: No "i18n.currentLang is not a function" or similar crashes\n');

console.log('✅ Static verification PASSED');
console.log('   Manual smoke test required (documented above)');
