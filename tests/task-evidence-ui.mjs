// Renderer regression only; no connection to the user's desktop or switches.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = fs.readFileSync('desktop/TaskEvidence.tsx', 'utf8');
const output = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS } }).outputText;
const module = { exports: {} };
new Function('require', 'module', 'exports', output)(require, module, module.exports);
const TaskEvidence = module.exports.default;
const base = { id: 'fixture', title: 'Fixture', workspace: 'fixture', model: 'fixture', mode: 'coding', status: 'cancelled' };
const render = task => renderToStaticMarkup(React.createElement(TaskEvidence, { task }));

const stopped = render({ ...base, telemetry: {
  tokens: { total: 120, interrupted_request: true }, budget: { metered_tokens: 3000, warning: false },
  bash: { calls: 1, failure_count: 0 },
  workspace_changes: { added: ['preserved.json'], modified: [], deleted: [], complete: true, out_of_scope: [] },
}});
assert(stopped.includes('120 tokens'));
assert(stopped.includes('本地计量（含估算）'));
assert(stopped.includes('preserved.json'));
assert(stopped.includes('中断请求未返回完整用量'));
const unknown = render({ ...base, telemetry: { tokens: { total: null }, budget: { metered_tokens: 500, warning: false } } });
assert(unknown.includes('未返回'));
assert.equal(unknown.match(/供应商已报告用量<\/dt><dd>([^<]+)<\/dd>/)?.[1], '未返回');
assert(render({ ...base, status: 'completed', verification: { status: 'pending' } }).includes('待 Codex 复验'));
assert(render({ ...base, status: 'completed', verification: { status: 'passed', evidence: '49 tests passed' } }).includes('复验通过'));
assert(render({ ...base, status: 'completed', verification: { status: 'failed', evidence: 'counterexample' } }).includes('复验未通过'));
assert.equal(render(base), '');
console.log(JSON.stringify({ ok: true, checks: ['cancelled task retains visible usage and files', 'missing usage is unknown rather than zero', 'pending/passed/failed verification are distinct', 'legacy records still render'] }, null, 2));
