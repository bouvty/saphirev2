const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const Module=require('node:module');
const ts=require('typescript');
const {NextRequest}=require('next/server');
const file=path.join(__dirname,'../app/auth/v2/discord/callback-handler.ts');
const loaded=new Module(file,module);
loaded.filename=file;
loaded.paths=Module._nodeModulePaths(path.dirname(file));
loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,esModuleInterop:true}}).outputText,file);
const {registrationCallback}=loaded.exports;
const state='v2_'+'s'.repeat(43),receipt='r'.repeat(43);
process.env.NEXT_PUBLIC_SITE_URL='https://saphire.rip';
process.env.RAILWAY_API_URL='https://backend.example';
const request=(cookie=state,extra='code=test')=>new NextRequest('https://saphire.rip/auth/callback?state='+state+'&'+extra,{headers:{cookie:'__Host-saphire_registration_state='+cookie}});
test('matching browser and verified backend result issue a secure success receipt',async()=>{
 global.fetch=async()=>Response.json({success_key:receipt});
 const response=await registrationCallback(request());
 assert.equal(response.status,303);
 assert.equal(response.headers.get('location'),'https://saphire.rip/auth/v2/discord/success/'+receipt);
 const cookie=response.cookies.get('__Host-saphire_registration_receipt');
 assert.equal(cookie.value,receipt);
 assert.equal(cookie.httpOnly,true);assert.equal(cookie.secure,true);assert.equal(cookie.sameSite,'lax');
 assert.equal(response.headers.get('referrer-policy'),'no-referrer');
 assert.equal(response.headers.get('cache-control'),'no-store');
});
test('a different browser cannot exchange the authorization code',async()=>{
 let calls=0;global.fetch=async()=>{calls++;throw Error('must not call')};
 const response=await registrationCallback(request('another-browser'));
 assert.equal(calls,0);assert.ok(response.headers.get('location').endsWith('/denied/'));
});
test('cancellation cannot create a success receipt',async()=>{
 global.fetch=async url=>{assert.equal(url.searchParams.get('error'),'access_denied');return Response.json({error:'denied'},{status:400})};
 const response=await registrationCallback(request(state,'error=access_denied'));
 assert.ok(response.headers.get('location').endsWith('/denied/'));
 assert.equal(response.cookies.get('__Host-saphire_registration_receipt').value,'');
});
test('malformed backend success is rejected',async()=>{
 global.fetch=async()=>Response.json({success_key:'unvalidated'});
 assert.ok((await registrationCallback(request())).headers.get('location').endsWith('/denied/'));
});
test('a backend outage fails closed',async()=>{
 global.fetch=async()=>{throw Error('offline')};
 assert.ok((await registrationCallback(request())).headers.get('location').endsWith('/denied/'));
});
