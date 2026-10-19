import {NextRequest,NextResponse} from 'next/server';
export function GET(req:NextRequest){
 const api=process.env.RAILWAY_API_URL;
 const id=req.nextUrl.searchParams.get('discord_id');
 const token=req.nextUrl.searchParams.get('token');
 if(!api||!id||!/^\d{15,22}$/.test(id)||!token||!/^[0-9a-f]{64}$/.test(token))return NextResponse.json({error:'Invalid registration link'},{status:400});
 return NextResponse.redirect(api.replace(/\/$/,'')+'/auth/start?discord_id='+encodeURIComponent(id)+'&token='+encodeURIComponent(token));
}