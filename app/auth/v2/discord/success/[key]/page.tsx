import {cookies} from 'next/headers';
import {redirect} from 'next/navigation';
import {timingSafeEqual} from 'node:crypto';
import {CheckCircle2,ArrowUpRight,ShieldCheck} from 'lucide-react';
export const dynamic='force-dynamic';
export const metadata={title:'Discord verified | Saphire',referrer:'no-referrer' as const};
const denied='/auth/v2/discord/denied/';
export default async function Success({params}:{params:Promise<{key:string}>}){
 const {key}=await params;
 const receipt=(await cookies()).get('__Host-saphire_registration_receipt')?.value||'';
 if(!/^[A-Za-z0-9_-]{43}$/.test(key)||receipt.length!==key.length||!timingSafeEqual(Buffer.from(receipt),Buffer.from(key)))redirect(denied);
 const api=process.env.RAILWAY_API_URL,token=process.env.BOT_INTERNAL_TOKEN;
 if(!api||!token)redirect(denied);
 let identity='';
 try{
  const response=await fetch(api.replace(/\/$/,'')+'/internal/auth/v2/discord/success/'+key,{headers:{Authorization:'Bearer '+token},cache:'no-store',signal:AbortSignal.timeout(12000)});
  if(response.ok){const result=await response.json();if(result.authorized===true&&/^\d{15,22}$/.test(result.discord_id))identity=result.discord_id;}
 }catch{}
 if(!identity)redirect(denied);
 return <div className="wrap"><header className="header"><a href="/" className="brand"><span className="mark">S</span>saphire.</a><span className="pill"><ShieldCheck size={14} aria-hidden="true"/> Verified by Discord</span></header><main className="center"><section className="notice-card" aria-labelledby="success-title"><div className="notice-icon"><CheckCircle2 size={30} aria-hidden="true"/></div><span className="eyebrow">Discord authorization complete</span><h1 id="success-title">Account <span className="gradient">verified.</span></h1><p className="sub">Your Discord account has been authorized successfully. Return to the Saphire bot; your registration status will update automatically.</p><div className="pill" style={{marginBottom:24,overflowWrap:'anywhere'}}>Discord ID: {identity}</div><br/><a className="btn" href="https://discord.gg/saphiree"><ArrowUpRight size={18} aria-hidden="true"/> Return to Discord</a></section></main><footer className="footer">© 2026 Saphire · Secure Discord verification</footer></div>;
}
