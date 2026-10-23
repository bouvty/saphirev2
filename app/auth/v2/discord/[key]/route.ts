import {NextRequest,NextResponse} from 'next/server';
export const dynamic='force-dynamic';
const root=()=> (process.env.NEXT_PUBLIC_SITE_URL||'https://saphire.rip').replace(/\/$/,'');
const denied=()=>NextResponse.redirect(root()+'/auth/v2/discord/denied/',303);
export async function GET(req:NextRequest,{params}:{params:Promise<{key:string}>}){
 const {key}=await params;
 if(!/^[A-Za-z0-9_-]{43}$/.test(key))return denied();
 const api=process.env.RAILWAY_API_URL;
 if(!api)return denied();
 try{
  const response=await fetch(api.replace(/\/$/,'')+'/auth/v2/discord/'+key,{redirect:'manual',cache:'no-store',signal:AbortSignal.timeout(12000)});
  if(response.status!==302)return denied();
  const location=new URL(response.headers.get('location')||'');
  const state=location.searchParams.get('state')||'';
  if(location.protocol!=='https:'||location.hostname!=='discord.com'||location.pathname!=='/oauth2/authorize'||!/^v2_[A-Za-z0-9_-]{43}$/.test(state))return denied();
  const out=NextResponse.redirect(location,302);
  out.headers.set('Cache-Control','no-store');out.headers.set('Referrer-Policy','no-referrer');
  out.cookies.set('__Host-saphire_registration_state',state,{httpOnly:true,secure:true,sameSite:'lax',path:'/',maxAge:600});
  out.cookies.set('__Host-saphire_registration_receipt','',{httpOnly:true,secure:true,sameSite:'lax',path:'/',maxAge:0});
  return out;
 }catch{return denied()}
}
