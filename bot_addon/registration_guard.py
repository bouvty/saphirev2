"""Integrate with your existing discord.py bot; this is not a standalone bot."""
import discord
async def require_registered(interaction, pool, auth_url):
    async with pool.acquire() as conn:
        exists=await conn.fetchval('SELECT EXISTS(SELECT 1 FROM accounts WHERE discord_id=$1)',str(interaction.user.id))
    if exists: return True
    embed=discord.Embed(title='Welcome to Saphire',description='Create an account to use Saphire. Connect your Discord account securely.',color=0x9A61E8)
    view=discord.ui.View(timeout=180)
    view.add_item(discord.ui.Button(label='Connect Discord',style=discord.ButtonStyle.link,url=auth_url))
    await interaction.response.send_message(embed=embed,view=view,ephemeral=True)
    return False
# auth_url must contain signed, user-bound, expiring OAuth state.
