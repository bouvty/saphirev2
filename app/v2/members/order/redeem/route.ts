import {NextRequest,NextResponse} from 'next/server';
import {timingSafeEqual} from 'node:crypto';
export const runtime='nodejs';
export const dynamic='force-dynamic';
const upstream='https://saphire-python-api-production.up.railway.app/members/order/redeem';

export async function POST(req:NextRequest){
 const expected=process.env.SAPHIRE_V2_API_KEY||'';
 const supplied=req.headers.get('x-api-key')||'';
 const a=Buffer.from(supplied),b=Buffer.from(expected);
 if(!expected||a.length!==b.length||!timingSafeEqual(a,b))
   return NextResponse.json({error:'Missing or invalid API key'},{status:401,headers:{'Cache-Control':'no-store'}});
 let raw:string;
 try{raw=await req.text()}catch{return NextResponse.json({error:'Invalid request'},{status:400})}
 if(raw.length>4096)return NextResponse.json({error:'Request too large'},{status:413});
 try {
  const result=await fetch(upstream,{method:'POST',headers:{'Content-Type':'application/json','X-API-Key':expected},body:raw,cache:'no-store',signal:AbortSignal.timeout(15000)});
  return new NextResponse(await result.arrayBuffer(),{status:result.status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
 } catch{return NextResponse.json({error:'Redeem service unavailable'},{status:502})}
}
export async function GET(){
 return new NextResponse('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Redeem • Saphire</title><style>body{margin:0;min-height:100vh;background:radial-gradient(circle at 50% 30%,#36363e,#09090a 65%);color:#fff;font:16px system-ui;display:grid;place-items:center;text-align:center}.panel{padding:60px 25px;max-width:520px}.star{font-size:88px;color:#ddd;text-shadow:0 0 30px #aaa}h1{font-size:42px;letter-spacing:-2px}p{color:#a1a1aa;line-height:1.8}a{display:inline-block;margin-top:20px;background:linear-gradient(#fff,#c8c8cf);color:#101013;padding:15px 24px;border-radius:12px;text-decoration:none;font-weight:700}</style></head><body><main class="panel"><div class="star">✦</div><h1>Redeem your Saphire key</h1><p>Open the Discord services panel and select <strong>Redeem key</strong>. Enter your Shoppex key and server invitation to process your order securely.</p><a href="https://discord.gg/saphiree">Join our Discord ↗</a></main></body></html>',{headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}})
}
