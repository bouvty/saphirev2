import {NextRequest,NextResponse} from 'next/server';
export async function POST(){return NextResponse.json({error:'Redeem keys through the authenticated Discord bot or server API; browser redemption requires a session.'},{status:401})}
