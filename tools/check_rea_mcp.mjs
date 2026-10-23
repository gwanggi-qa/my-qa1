// Metadata-only MCP handshake. Never forwards APK content to a model.
import {fileURLToPath, pathToFileURL} from 'node:url';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const modules = path.join(root, 'qa_results/tools/rea/node_modules');
const {Client} = await import(pathToFileURL(path.join(modules, '@modelcontextprotocol/client/dist/index.mjs')));
const {StdioClientTransport} = await import(pathToFileURL(path.join(modules, '@modelcontextprotocol/client/dist/stdio.mjs')));
const client = new Client({name:'bts-rea-environment-check',version:'1.0.0'});
const env = {};
for (const k of ['PATH','Path','PATHEXT','SYSTEMROOT','SystemRoot','WINDIR','TEMP','TMP','USERPROFILE','HOME','LOCALAPPDATA','APPDATA']) {
  if (process.env[k]) env[k] = process.env[k];
}
Object.assign(env, {JAVA_HOME: process.env.QA_ANALYSIS_JAVA_HOME || 'C:/Program Files/Android/Android Studio/jbr',
  REA_JADX_MCP_JAR:path.join(root,'qa_results/tools/jadx-headless-mcp-0.7.1-all.jar'),
  REA_JADX_HEAP_MIB:'2048', REA_JADX_ACTIVE_PROCESSOR_COUNT:'2'});
const transport = new StdioClientTransport({command:process.execPath,
  args:[path.join(modules,'rea-agents/scripts/rea.mjs'),'--mcp'],env,stderr:'pipe'});
try {
  await client.connect(transport);
  const {tools} = await client.listTools();
  const names = tools.map(t=>t.name).filter(n=>/android/.test(n));
  if (!names.includes('inspect_android_package')) throw Error('APK tool missing');
  console.log(JSON.stringify({connected:true,server:client.getServerVersion(),android_tools:names}));
} finally { await client.close(); }
