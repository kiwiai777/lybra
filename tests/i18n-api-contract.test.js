/**
 * AIPOS-288 FIX-3c: i18n API contract test
 * 
 * Scans all i18n.<method>() calls in JS/HTML files and asserts
 * that <method> exists in i18n.js exported API surface.
 * 
 * Prevents future "i18n.currentLang is not a function" crashes.
 */

const fs = require('fs');
const path = require('path');

// Extract exported API from i18n.js
function getI18nExports() {
  const i18nPath = path.join(__dirname, '../web/board/static/i18n.js');
  const content = fs.readFileSync(i18nPath, 'utf8');
  
  // Find window.i18n = { ... } block
  const match = content.match(/window\.i18n\s*=\s*\{([^}]+)\}/);
  if (!match) {
    throw new Error('Could not find window.i18n export in i18n.js');
  }
  
  // Extract method names (e.g., "t,", "switchLanguage,", etc.)
  const exports = match[1]
    .split(',')
    .map(s => s.trim())
    .filter(s => s && !s.startsWith('//'));
  
  return new Set(exports);
}

// Scan files for i18n.<method>() calls
function scanI18nCalls(dir, extensions = ['.js', '.html']) {
  const calls = new Map(); // method -> [file:line]
  
  function walk(currentPath) {
    const stat = fs.statSync(currentPath);
    if (stat.isDirectory()) {
      // Skip node_modules, .git, etc.
      const base = path.basename(currentPath);
      if (base.startsWith('.') || base === 'node_modules') return;
      
      for (const entry of fs.readdirSync(currentPath)) {
        walk(path.join(currentPath, entry));
      }
    } else if (stat.isFile()) {
      const ext = path.extname(currentPath);
      if (!extensions.includes(ext)) return;
      
      const content = fs.readFileSync(currentPath, 'utf8');
      const lines = content.split('\n');
      
      // Match i18n.<method>( but not i18n.t( which is special
      const pattern = /\bi18n\.(\w+)\s*\(/g;
      
      lines.forEach((line, idx) => {
        let match;
        while ((match = pattern.exec(line)) !== null) {
          const method = match[1];
          const location = `${path.relative(dir, currentPath)}:${idx + 1}`;
          
          if (!calls.has(method)) {
            calls.set(method, []);
          }
          calls.get(method).push(location);
        }
      });
    }
  }
  
  walk(dir);
  return calls;
}

// Main test
function main() {
  const webDir = path.join(__dirname, '../web/board/static');
  
  console.log('📋 Loading i18n.js exports...');
  const exports = getI18nExports();
  console.log(`   Found: ${Array.from(exports).join(', ')}`);
  
  console.log('\n🔍 Scanning for i18n.<method>() calls...');
  const calls = scanI18nCalls(webDir);
  
  let violations = [];
  
  for (const [method, locations] of calls.entries()) {
    if (!exports.has(method)) {
      violations.push({ method, locations });
    }
  }
  
  if (violations.length > 0) {
    console.error('\n❌ API CONTRACT VIOLATIONS:');
    for (const { method, locations } of violations) {
      console.error(`\n   i18n.${method}() is NOT exported by i18n.js`);
      console.error(`   Called at:`);
      for (const loc of locations) {
        console.error(`      ${loc}`);
      }
    }
    console.error(`\n   Exported API: ${Array.from(exports).join(', ')}`);
    process.exit(1);
  }
  
  console.log('\n✅ All i18n.<method>() calls match exported API');
  console.log(`   Verified ${calls.size} distinct method(s) across ${
    Array.from(calls.values()).reduce((sum, locs) => sum + locs.length, 0)
  } call site(s)`);
}

if (require.main === module) {
  main();
}

module.exports = { getI18nExports, scanI18nCalls };
