import {NextRequest,NextResponse} from 'next/server';import {timingSafeEqual} from 'crypto';import {base,sign} from '@/lib/auth';
export async function GET(req:NextRequest){
const redirect=(path:string)=>NextResponse.redirect(base()+path);
const state=req.nextUrl.searchParams.get('state'),expected=req.cookies.get('oauth_state')?.value,code=req.nextUrl.searchParams.get('code');
if(!state||!code)return redirect('/login?error=state');
if(!expected){
 const api=process.env.RAILWAY_API_URL;
 if(!api)return redirect('/login?error=state');
 // Bot OAuth session is validated by Railway using its single-use stored state.
 return NextResponse.redirect(api.replace(/\/$/,'')+'/auth/callback?code='+encodeURIComponent(code)+'&state='+encodeURIComponent(state));
}
if(state.length!==expected.length||!timingSafeEqual(Buffer.from(state),Buffer.from(expected)))return redirect('/login?error=state');
try{
const form=new URLSearchParams({client_id:process.env.DISCORD_CLIENT_ID||'',client_secret:process.env.DISCORD_CLIENT_SECRET||'',grant_type:'authorization_code',code,redirect_uri:base()+'/auth/callback'});
const result=await fetch('https://discord.com/api/v10/oauth2/token',{method:'POST',body:form,headers:{'Content-Type':'application/x-www-form-urlencoded'},cache:'no-store'});if(!result.ok)return redirect('/login?error=oauth');
const token=await result.json();const response=await fetch('https://discord.com/api/v10/users/@me',{headers:{Authorization:'Bearer '+token.access_token},cache:'no-store'});if(!response.ok)return redirect('/login?error=profile');
const u=await response.json(),jwt=await sign({id:u.id,username:u.username,global_name:u.global_name,avatar:u.avatar});const next=req.cookies.get('oauth_next')?.value||'/auth/confirm';const r=redirect(next.startsWith('/')&&!next.startsWith('//')?next:'/auth/confirm');r.cookies.set('saphire_session',jwt,{httpOnly:true,secure:process.env.NODE_ENV==='production',sameSite:'lax',maxAge:28800,path:'/'});r.cookies.delete('oauth_state');r.cookies.delete('oauth_next');return r
}catch{return redirect('/login?error=server')}
}