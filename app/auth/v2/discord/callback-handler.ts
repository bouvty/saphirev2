import {NextRequest,NextResponse} from 'next/server';
import {timingSafeEqual} from 'node:crypto';
export async function registrationCallback(req:NextRequest){
 const root=(process.env.NEXT_PUBLIC_SITE_URL||'https://saphire.rip').replace(/\/$/,'');
 const deny=()=>{
  const out=NextResponse.redirect(root+'/auth/v2/discord/denied/',303);
  out.headers.set('Cache-Control','no-store');out.headers.set('Referrer-Policy','no-referrer');
  for(const name of ['__Host-saphire_registration_state','__Host-saphire_registration_receipt'])out.cookies.set(name,'',{httpOnly:true,secure:true,sameSite:'lax',path:'/',maxAge:0});
  return out;
 };
 const state=req.nextUrl.searchParams.get('state')||'';
 const expected=req.cookies.get('__Host-saphire_registration_state')?.value||'';
 const api=process.env.RAILWAY_API_URL;
 if(!/^v2_[A-Za-z0-9_-]{43}$/.test(state)||expected.length!==state.length||!timingSafeEqual(Buffer.from(expected),Buffer.from(state))||!api)return deny();
 try{
  const url=new URL('/auth/v2/discord/callback',api);
  for(const name of ['state','code','error','iss']){const value=req.nextUrl.searchParams.get(name);if(value)url.searchParams.set(name,value);}
  const response=await fetch(url,{redirect:'manual',cache:'no-store',signal:AbortSignal.timeout(25000)});
  if(!response.ok)return deny();
  const result=await response.json();
  if(typeof result.success_key!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(result.success_key))return deny();
  const out=NextResponse.redirect(root+'/auth/v2/discord/success/'+result.success_key,303);
  out.headers.set('Cache-Control','no-store');out.headers.set('Referrer-Policy','no-referrer');
  out.cookies.set('__Host-saphire_registration_state','',{httpOnly:true,secure:true,sameSite:'lax',path:'/',maxAge:0});
  out.cookies.set('__Host-saphire_registration_receipt',result.success_key,{httpOnly:true,secure:true,sameSite:'lax',path:'/',maxAge:300});
  return out;
 }catch{return deny()}
}
