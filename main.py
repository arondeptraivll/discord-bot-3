import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime
from collections import defaultdict, deque, Counter
import asyncio

# --- CẤU HÌNH ---
# Tải token từ biến môi trường (an toàn hơn cho Render)
TOKEN = os.getenv("DISCORD_TOKEN") 
# ID kênh để ghi log
LOG_CHANNEL_ID = 1387283127793225809
# Tên role "Muted" - ĐÃ CẬP NHẬT THEO YÊU CẦU CỦA BẠN
MUTED_ROLE_NAME = "Muted 🤐" 

# --- CẤU HÌNH CHỐNG SPAM KHẮT KHE ---
SPAM_CONFIG = {
    'rate_limit_count': 7,      # Số tin nhắn để tính là spam nhanh
    'rate_limit_seconds': 4,    # Trong khoảng thời gian (giây)
    'duplicate_count': 3,       # Số tin nhắn giống hệt nhau liên tiếp để tính là spam
    'max_mentions': 5,          # Số lượng đề cập tối đa trong một tin nhắn
    'caps_ratio': 0.7,          # Tỷ lệ chữ IN HOA (70%)
    'min_caps_length': 15,      # Độ dài tối thiểu của tin nhắn để kiểm tra IN HOA
    'min_word_spam_length': 10, # Tin nhắn phải có ít nhất 10 từ để kiểm tra lặp từ
    'word_spam_ratio': 0.5,     # Nếu 1 từ chiếm 50% tin nhắn -> spam
    'mute_duration_hours': 3    # Thời gian mute là 3 giờ
}

# --- KHỞI TẠO BOT VÀ BIẾN TOÀN CỤC ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Cấu trúc dữ liệu theo dõi vi phạm của người dùng
user_spam_data = defaultdict(lambda: {
    'recent_messages': deque(maxlen=SPAM_CONFIG['rate_limit_count']), 
    'warnings': 0 # Số lần vi phạm để áp dụng hình phạt theo cấp độ
})

# Nhập hàm keep_alive từ file keep_alive.py
from keep_alive import keep_alive

# --- SỰ KIỆN BOT ---
@bot.event
async def on_ready():
    print(f'Bot đã đăng nhập với tên {bot.user}')
    print('-----------------------------------------')
    print('Bot phiên bản NÂNG CẤP: Hình phạt theo cấp độ & Tự động dọn dẹp.')
    print(f'Sử dụng role mute tên: "{MUTED_ROLE_NAME}"')
    print('-----------------------------------------')
    try:
        synced = await bot.tree.sync()
        print(f"Đã đồng bộ {len(synced)} lệnh (/)")
    except Exception as e:
        print(e)
    # Lấy kênh log một lần khi sẵn sàng
    global log_channel
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        print(f"LỖI: Không tìm thấy kênh log với ID: {LOG_CHANNEL_ID}. Vui lòng kiểm tra lại ID.")

@bot.event
async def on_message(message):
    # Bỏ qua tin nhắn từ bot, tin nhắn riêng, hoặc từ người có quyền quản lý
    if not message.guild or message.author.bot or message.author.guild_permissions.manage_messages:
        return

    author_id = message.author.id
    user_data = user_spam_data[author_id]
    
    user_data['recent_messages'].append(message)
    recent_messages = list(user_data['recent_messages'])
    content = message.content.lower()

    # --- LOGIC CHỐNG SPAM NÂNG CẤP ---
    
    # 1. KIỂM TRA SPAM TIN NHẮN NHANH (7 tin / 4s)
    if len(recent_messages) == SPAM_CONFIG['rate_limit_count']:
        time_diff = (recent_messages[-1].created_at - recent_messages[0].created_at).total_seconds()
        if time_diff < SPAM_CONFIG['rate_limit_seconds']:
            await handle_spam(recent_messages, "Spam tin nhắn quá nhanh")
            user_data['recent_messages'].clear()
            return

    # 2. KIỂM TRA SPAM TIN NHẮN TRÙNG LẶP (3 tin giống nhau)
    if len(recent_messages) >= SPAM_CONFIG['duplicate_count']:
        last_n_messages = recent_messages[-SPAM_CONFIG['duplicate_count']:]
        if len({msg.content for msg in last_n_messages}) == 1 and last_n_messages[0].content != "":
            await handle_spam(last_n_messages, "Spam tin nhắn trùng lặp")
            user_data['recent_messages'].clear()
            return
            
    # 3. KIỂM TRA SPAM LẶP TỪ
    words = content.split()
    if len(words) >= SPAM_CONFIG['min_word_spam_length']:
        word_counts = Counter(words)
        most_common_word_count = word_counts.most_common(1)[0][1]
        if (most_common_word_count / len(words)) >= SPAM_CONFIG['word_spam_ratio']:
            await handle_spam([message], f"Spam lặp từ (từ \"{word_counts.most_common(1)[0][0]}\" chiếm >{int(SPAM_CONFIG['word_spam_ratio']*100)}% tin nhắn)")
            return
    
    # 4. KIỂM TRA SPAM ĐỀ CẬP (MENTION SPAM)
    if len(message.mentions) + len(message.role_mentions) > SPAM_CONFIG['max_mentions']:
        await handle_spam([message], "Spam đề cập (mention)")
        return
    
    # 5. KIỂM TRA SPAM CHỮ IN HOA (CAPS SPAM)
    if len(content) > SPAM_CONFIG['min_caps_length']:
        uppercase_chars = sum(1 for char in message.content if char.isupper())
        alpha_chars = sum(1 for char in message.content if char.isalpha())
        if alpha_chars > 0 and (uppercase_chars / alpha_chars) > SPAM_CONFIG['caps_ratio']:
            await handle_spam([message], f"Gửi tin nhắn có tỷ lệ chữ IN HOA quá cao (>{int(SPAM_CONFIG['caps_ratio']*100)}%)")
            return

    await bot.process_commands(message)

# --- HÀM XỬ LÝ SPAM VỚI HÌNH PHẠT THEO CẤP ĐỘ VÀ TỰ ĐỘNG DỌN DẸP ---
async def handle_spam(messages_to_delete: list[discord.Message], reason: str):
    if not messages_to_delete:
        return

    author = messages_to_delete[0].author
    channel = messages_to_delete[0].channel
    guild = channel.guild
    author_id = author.id
    user_data = user_spam_data[author_id]

    # --- BƯỚC 1: XÓA TIN NHẮN VI PHẠM (NHỮNG TIN GÂY TRIGGER) ---
    triggering_messages_count = len(messages_to_delete)
    try:
        await channel.purge(limit=triggering_messages_count + 1, check=lambda m: m in messages_to_delete)
    except discord.Forbidden:
        if log_channel: await log_channel.send(f"**LỖI:** Bot không có quyền `Manage Messages` để xóa tin nhắn trong kênh {channel.mention}.")
        return
    except discord.HTTPException as e:
        if log_channel: await log_channel.send(f"**LỖI:** Không thể xóa tin nhắn vi phạm: `{e}`")
        return

    # --- BƯỚC 2: TỰ ĐỘNG DỌN DẸP TIN NHẮN TRONG 1 GIỜ QUA ---
    purged_in_hour_count = 0
    try:
        one_hour_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        purged_messages = await channel.purge(limit=200, check=lambda m: m.author == author, after=one_hour_ago)
        purged_in_hour_count = len(purged_messages)
    except discord.Forbidden:
        if log_channel: await log_channel.send(f"**CẢNH BÁO:** Bot không có quyền để xóa lịch sử tin nhắn của {author.mention} trong kênh {channel.mention}.")
    except discord.HTTPException as e:
        if log_channel: await log_channel.send(f"**LỖI:** Không thể xóa lịch sử tin nhắn: `{e}`")
    
    total_deleted_count = triggering_messages_count + purged_in_hour_count

    # --- BƯỚC 3: TĂNG CẢNH CÁO VÀ ÁP DỤNG HÌNH PHẠT ---
    user_data['warnings'] += 1
    warning_count = user_data['warnings']

    # CẤP 1: CẢNH CÁO
    if warning_count == 1:
        embed = discord.Embed(
            title="⚠️ CẢNH CÁO SPAM (Lần 1) ⚠️",
            description=f"**Người dùng:** {author.mention}\n**Lý do:** {reason}\n**Hành động:** Đã xóa **{total_deleted_count} tin nhắn** (bao gồm tin nhắn trong 1 giờ qua) và gửi cảnh cáo qua DM.",
            color=discord.Color.yellow()
        )
        embed.set_footer(text=f"Trong kênh: #{channel.name}")
        if log_channel: await log_channel.send(embed=embed)
        try:
            await author.send(
                f"Bạn nhận được **cảnh cáo lần 1** tại server `{guild.name}` vì lý do: **{reason}**.\n"
                f"Hệ thống đã tự động xóa **{total_deleted_count} tin nhắn** của bạn. "
                f"Nếu tiếp tục vi phạm, bạn sẽ bị **câm lặng tạm thời**."
            )
        except discord.Forbidden: pass

    # CẤP 2: MUTE 3 GIỜ
    elif warning_count == 2:
        muted_role = discord.utils.get(guild.roles, name=MUTED_ROLE_NAME)
        if not muted_role:
            if log_channel: await log_channel.send(f"**LỖI:** Không tìm thấy role `{MUTED_ROLE_NAME}` để mute. Hãy đảm bảo role này tồn tại và tên chính xác.")
            return

        embed = discord.Embed(title="🚫 TỰ ĐỘNG MUTE (Lần 2) 🚫", color=discord.Color.orange())
        embed.description=f"**Người dùng:** {author.mention}\n**Lý do:** {reason}\n**Hành động:** Đã xóa **{total_deleted_count} tin nhắn** và mute **{SPAM_CONFIG['mute_duration_hours']} giờ**."
        if log_channel: await log_channel.send(embed=embed)
        
        try:
            await author.add_roles(muted_role, reason=f"Tự động mute do vi phạm spam lần 2. ({reason})")
            await author.send(
                f"Bạn đã bị **câm lặng trong {SPAM_CONFIG['mute_duration_hours']} giờ** tại server `{guild.name}` do vi phạm spam lần 2.\n"
                f"Lý do: **{reason}**. Nếu vi phạm lần nữa, bạn sẽ bị **BAN**."
            )
            
            await asyncio.sleep(SPAM_CONFIG['mute_duration_hours'] * 3600)
            
            fresh_member = await guild.fetch_member(author_id)
            if muted_role in fresh_member.roles:
                await fresh_member.remove_roles(muted_role, reason="Tự động gỡ mute.")
                if log_channel: await log_channel.send(f"✅ Đã tự động gỡ mute cho {author.mention}.")
                try: await author.send(f"Bạn đã được gỡ mute tại server `{guild.name}`.")
                except discord.Forbidden: pass
        except discord.Forbidden:
            if log_channel: await log_channel.send(f"**LỖI:** Bot không có quyền để mute {author.mention}. Hãy kiểm tra quyền 'Manage Roles' và thứ hạng của role bot.")
        except Exception as e:
            if log_channel: await log_channel.send(f"**LỖI BẤT NGỜ KHI MUTE:** {e}")

    # CẤP 3: BAN
    elif warning_count >= 3:
        embed = discord.Embed(title="🔨 TỰ ĐỘNG BAN (Lần 3) 🔨", color=discord.Color.red())
        embed.description=f"**Người dùng:** {author.mention} (`{author.id}`)\n**Lý do:** {reason}\n**Hành động:** Tái phạm nhiều lần, **BAN vĩnh viễn**. Đã xóa **{total_deleted_count} tin nhắn** trước khi ban."
        if log_channel: await log_channel.send(embed=embed)

        try:
            await author.send(f"Bạn đã bị **BAN vĩnh viễn** khỏi server `{guild.name}` do vi phạm các quy định về spam quá nhiều lần.")
        except discord.Forbidden: pass
        
        try:
            await guild.ban(author, reason=f"Tự động ban do vi phạm spam lần 3. (Lý do cuối: {reason})", delete_message_days=1)
            user_spam_data.pop(author_id, None)
        except discord.Forbidden:
             if log_channel: await log_channel.send(f"**LỖI:** Bot không có quyền để BAN {author.mention}. Hãy kiểm tra quyền 'Ban Members'.")
        except Exception as e:
            if log_channel: await log_channel.send(f"**LỖI BẤT NGỜ KHI BAN:** {e}")

# --- CÁC LỆNH SLASH (/) ---

@bot.tree.command(name="purge_user", description="Xóa tin nhắn của một thành viên trong một khoảng thời gian.")
@app_commands.describe(member="Thành viên có tin nhắn cần xóa.", hours="Xóa tin nhắn trong bao nhiêu giờ qua? (Mặc định là 24)")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge_user(interaction: discord.Interaction, member: discord.Member, hours: int = 24):
    await interaction.response.defer(ephemeral=True)
    if hours > 336: # 14 * 24 = 336
        await interaction.followup.send("Không thể xóa tin nhắn cũ hơn 14 ngày.", ephemeral=True)
        return

    after_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    
    try:
        deleted = await interaction.channel.purge(limit=None, check=lambda m: m.author == member, after=after_time)
        deleted_count = len(deleted)
    except Exception as e:
        await interaction.followup.send(f"Đã có lỗi xảy ra: {e}", ephemeral=True)
        return

    await interaction.followup.send(f"Đã xóa thành công {deleted_count} tin nhắn của {member.mention} trong {hours} giờ qua.", ephemeral=True)
    if log_channel:
        embed = discord.Embed(title="Xóa tin nhắn thủ công", color=discord.Color.blue())
        embed.add_field(name="Thành viên", value=member.mention, inline=False)
        embed.add_field(name="Người thực hiện", value=interaction.user.mention, inline=False)
        embed.add_field(name="Số lượng", value=f"{deleted_count} tin nhắn trong {hours} giờ", inline=False)
        await log_channel.send(embed=embed)

@bot.tree.command(name="kick", description="Kick một thành viên ra khỏi server.")
@app_commands.describe(member="Thành viên cần kick", reason="Lý do kick")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Không có lý do"):
    if member.guild_permissions.administrator:
        await interaction.response.send_message("Bạn không thể kick Quản trị viên!", ephemeral=True)
        return
    await member.kick(reason=reason)
    await interaction.response.send_message(f"Đã kick {member.mention}. Lý do: {reason}")

@bot.tree.command(name="ban", description="Cấm một thành viên truy cập server.")
@app_commands.describe(member="Thành viên cần ban", reason="Lý do ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "Không có lý do"):
    if member.guild_permissions.administrator:
        await interaction.response.send_message("Bạn không thể ban Quản trị viên!", ephemeral=True)
        return
    await member.ban(reason=reason, delete_message_days=1)
    await interaction.response.send_message(f"Đã ban {member.mention}. Lý do: {reason}")

@bot.tree.command(name="clear", description="Xóa một số lượng tin nhắn nhất định.")
@app_commands.describe(amount="Số lượng tin nhắn cần xóa (tối đa 100).")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    if amount <= 0 or amount > 100:
        await interaction.response.send_message("Vui lòng nhập một số từ 1 đến 100.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Đã xóa thành công {len(deleted)} tin nhắn.", ephemeral=True)

@bot.tree.command(name="mute", description="Câm lặng một thành viên trong một khoảng thời gian.")
@app_commands.describe(member="Thành viên cần mute", minutes="Số phút muốn mute", reason="Lý do mute")
@app_commands.checks.has_permissions(manage_roles=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "Không có lý do"):
    if member == interaction.user or member.guild_permissions.administrator:
        await interaction.response.send_message("Bạn không thể mute chính mình hoặc Quản trị viên!", ephemeral=True)
        return
    muted_role = discord.utils.get(interaction.guild.roles, name=MUTED_ROLE_NAME)
    if not muted_role:
        await interaction.response.send_message(f"Không tìm thấy role `{MUTED_ROLE_NAME}`!", ephemeral=True)
        return
    await member.add_roles(muted_role, reason=reason)
    await interaction.response.send_message(f"Đã mute {member.mention} trong {minutes} phút. Lý do: {reason}")
    if minutes > 0:
        await asyncio.sleep(minutes * 60)
        if muted_role in member.roles:
            await member.remove_roles(muted_role, reason="Hết thời gian mute.")
            if log_channel:
                await log_channel.send(f"Đã tự động gỡ mute cho {member.mention} sau khi hết hạn.")

@bot.tree.command(name="unmute", description="Gỡ câm lặng cho một thành viên.")
@app_commands.describe(member="Thành viên cần gỡ mute", reason="Lý do gỡ mute")
@app_commands.checks.has_permissions(manage_roles=True)
async def unmute(interaction: discord.Interaction, member: discord.Member, reason: str = "Gỡ mute thủ công"):
    muted_role = discord.utils.get(interaction.guild.roles, name=MUTED_ROLE_NAME)
    if not muted_role or muted_role not in member.roles:
        await interaction.response.send_message(f"{member.mention} không bị mute hoặc không tìm thấy role `{MUTED_ROLE_NAME}`.", ephemeral=True)
        return
    await member.remove_roles(muted_role, reason=reason)
    await interaction.response.send_message(f"Đã gỡ mute cho {member.mention}.")

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("Bạn không có quyền để sử dụng lệnh này!", ephemeral=True)
    else:
        print(f"Lỗi lệnh slash không xác định: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Đã có lỗi xảy ra khi thực hiện lệnh.", ephemeral=True)
        else:
            await interaction.followup.send("Đã có lỗi xảy ra khi thực hiện lệnh.", ephemeral=True)

# --- CHẠY BOT ---
keep_alive() # Bắt đầu chạy web server để host trên Render
bot.run(TOKEN) # Bắt đầu chạy bot
