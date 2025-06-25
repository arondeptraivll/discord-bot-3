import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime
from collections import defaultdict, deque
import asyncio

# --- CẤU HÌNH ---
# Tải token từ biến môi trường (an toàn hơn cho Render)
TOKEN = os.getenv("DISCORD_TOKEN") 
# ID kênh để ghi log - ĐÃ CẬP NHẬT THEO YÊU CẦU CỦA BẠN
LOG_CHANNEL_ID = 1387283127793225809
# Tên role "Muted"
MUTED_ROLE_NAME = "Muted" 

# Cấu hình chống spam - BẠN CÓ THỂ CHỈNH CÁC THÔNG SỐ NÀY
SPAM_CONFIG = {
    'rate_limit_count': 5,  # Số tin nhắn để tính là spam nhanh
    'rate_limit_seconds': 4, # Trong khoảng thời gian (giây) - Tăng nhẹ để có thêm thời gian xử lý
    'duplicate_count': 3,   # Số tin nhắn giống hệt nhau liên tiếp để tính là spam
    'max_mentions': 5,      # Số lượng đề cập tối đa trong một tin nhắn
    'caps_ratio': 0.7,      # Tỷ lệ chữ IN HOA (70%) để tính là spam
    'min_caps_length': 15,  # Độ dài tối thiểu của tin nhắn để kiểm tra IN HOA
    'warning_limit': 3,     # Số lần cảnh cáo trước khi tự động mute
    'mute_duration_minutes': 10 # Thời gian mute (phút)
}

# --- KHỞI TẠO BOT VÀ BIẾN TOÀN CỤC ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Cấu trúc dữ liệu đã được cải tiến để lưu trữ đối tượng Message
user_spam_data = defaultdict(lambda: {
    # Lưu trữ các đối tượng Message gần đây để có thể xóa hàng loạt
    'recent_messages': deque(maxlen=SPAM_CONFIG['rate_limit_count']), 
    'warnings': 0
})

# Nhập hàm keep_alive từ file kia
from keep_alive import keep_alive

# --- SỰ KIỆN BOT ---
@bot.event
async def on_ready():
    print(f'Bot đã đăng nhập với tên {bot.user}')
    print('-----------------------------------------')
    print('Bot phiên bản cải tiến: Xóa hàng loạt tin nhắn spam.')
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
    # --- BỘ LỌC CƠ BẢN ---
    if not message.guild or message.author.bot or message.author.guild_permissions.manage_messages:
        return

    author_id = message.author.id
    user_data = user_spam_data[author_id]
    
    # Thêm đối tượng tin nhắn vào deque để theo dõi
    user_data['recent_messages'].append(message)
    recent_messages = list(user_data['recent_messages'])

    # --- LOGIC CHỐNG SPAM CẢI TIẾN ---
    
    # 1. KIỂM TRA SPAM TIN NHẮN NHANH (RATE LIMITING)
    if len(recent_messages) == SPAM_CONFIG['rate_limit_count']:
        # So sánh thời gian giữa tin nhắn mới nhất và cũ nhất trong deque
        time_diff = (recent_messages[-1].created_at - recent_messages[0].created_at).total_seconds()
        if time_diff < SPAM_CONFIG['rate_limit_seconds']:
            # Hành động: Xóa TOÀN BỘ cụm tin nhắn spam
            await handle_spam(recent_messages, "Spam tin nhắn quá nhanh")
            user_data['recent_messages'].clear() # Xóa deque sau khi xử lý để tránh trigger lại
            return

    # 2. KIỂM TRA SPAM TIN NHẮN TRÙNG LẶP
    # Chỉ kiểm tra nếu có đủ tin nhắn trong deque
    if len(recent_messages) >= SPAM_CONFIG['duplicate_count']:
        # Lấy N tin nhắn cuối cùng để kiểm tra trùng lặp
        last_n_messages = recent_messages[-SPAM_CONFIG['duplicate_count']:]
        contents = {msg.content for msg in last_n_messages}
        # Nếu tất cả nội dung là một (set có size 1) và nội dung không rỗng
        if len(contents) == 1 and last_n_messages[0].content != "":
            # Hành động: Xóa TOÀN BỘ cụm tin nhắn trùng lặp
            await handle_spam(last_n_messages, "Spam tin nhắn trùng lặp")
            user_data['recent_messages'].clear()
            return
    
    # 3. KIỂM TRA SPAM ĐỀ CẬP (MENTION SPAM) - Vẫn xử lý trên 1 tin
    if len(message.mentions) + len(message.role_mentions) > SPAM_CONFIG['max_mentions']:
        # Hành động: Xóa tin nhắn chứa spam mention
        await handle_spam([message], "Spam đề cập (mention)")
        return
    
    # 4. KIỂM TRA SPAM CHỮ IN HOA (CAPS SPAM) - Vẫn xử lý trên 1 tin
    content = message.content
    if len(content) > SPAM_CONFIG['min_caps_length']:
        uppercase_chars = sum(1 for char in content if char.isupper())
        alpha_chars = sum(1 for char in content if char.isalpha())
        if alpha_chars > 0 and (uppercase_chars / alpha_chars) > SPAM_CONFIG['caps_ratio']:
            # Hành động: Xóa tin nhắn viết IN HOA
            await handle_spam([message], f"Gửi tin nhắn có tỷ lệ chữ IN HOA quá cao (>{int(SPAM_CONFIG['caps_ratio']*100)}%)")
            return

    await bot.process_commands(message)

# --- HÀM HỖ TRỢ XỬ LÝ SPAM ĐÃ ĐƯỢC CẢI TIẾN ---
async def handle_spam(messages_to_delete: list[discord.Message], reason: str):
    """
    Hàm trung tâm để xử lý các hành vi spam.
    Giờ đây nhận một danh sách các tin nhắn để xóa hàng loạt.
    """
    if not messages_to_delete:
        return

    # Thông tin được lấy từ tin nhắn đầu tiên trong danh sách
    author = messages_to_delete[0].author
    channel = messages_to_delete[0].channel
    author_id = author.id
    user_data = user_spam_data[author_id]

    # **THAY ĐỔI LỚN: XÓA HÀNG LOẠT TIN NHẮN**
    try:
        # Sử dụng purge để xóa hàng loạt thay vì vòng lặp
        await channel.purge(limit=len(messages_to_delete) + 1, check=lambda m: m in messages_to_delete)
    except discord.Forbidden:
        if log_channel: await log_channel.send(f"**LỖI:** Bot không có quyền `Manage Messages` để xóa tin nhắn trong kênh {channel.mention}.")
        return # Không thể tiếp tục nếu không xóa được tin nhắn
    except discord.HTTPException as e:
        if log_channel: await log_channel.send(f"**LỖI:** Không thể xóa tin nhắn: `{e}`")
        return

    user_data['warnings'] += 1
    warning_count = user_data['warnings']
    
    # Embed để thông báo vẫn như cũ
    embed = discord.Embed(
        title="⚠️ Phát Hiện Hành Vi Spam (Cải tiến) ⚠️",
        description=f"**Người dùng:** {author.mention}\n**Lý do:** {reason}\n**Số tin nhắn đã xóa:** {len(messages_to_delete)}",
        color=discord.Color.orange()
    )
    embed.add_field(name="Cảnh cáo lần thứ", value=f"{warning_count}/{SPAM_CONFIG['warning_limit']}", inline=True)
    embed.set_footer(text=f"Trong kênh: #{channel.name}")
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    
    if log_channel:
        await log_channel.send(embed=embed)
        
    try:
        dm_channel = await author.create_dm()
        await dm_channel.send(
            f"Hệ thống đã tự động xóa **{len(messages_to_delete)} tin nhắn** của bạn tại server `{channel.guild.name}` vì lý do: **{reason}**.\n"
            f"Đây là cảnh cáo thứ **{warning_count}/{SPAM_CONFIG['warning_limit']}**. "
            f"Nếu tiếp tục vi phạm, bạn sẽ bị câm lặng tạm thời."
        )
    except discord.Forbidden:
        pass # Bỏ qua nếu người dùng khóa DM
        
    # Xử lý Mute nếu đạt đến giới hạn
    if warning_count >= SPAM_CONFIG['warning_limit']:
        user_data['warnings'] = 0 
        
        muted_role = discord.utils.get(channel.guild.roles, name=MUTED_ROLE_NAME)
        if not muted_role:
            if log_channel: await log_channel.send(f"**LỖI:** Không tìm thấy role `{MUTED_ROLE_NAME}`.")
            return

        try:
            await author.add_roles(muted_role, reason="Tự động mute do spam liên tục.")
            
            mute_embed = discord.Embed(
                title="🚫 Tự Động Mute 🚫",
                description=f"**Người dùng:** {author.mention} đã bị câm lặng.\n**Thời gian:** {SPAM_CONFIG['mute_duration_minutes']} phút.",
                color=discord.Color.red()
            )
            if log_channel: await log_channel.send(embed=mute_embed)
            
            await author.send(f"Bạn đã bị câm lặng tạm thời trong **{SPAM_CONFIG['mute_duration_minutes']} phút** tại server `{channel.guild.name}` do spam liên tục.")
            
            await asyncio.sleep(SPAM_CONFIG['mute_duration_minutes'] * 60)
            
            # Cần lấy lại đối tượng member mới vì đối tượng cũ có thể bị cache
            fresh_member = await channel.guild.fetch_member(author_id)
            if muted_role in fresh_member.roles:
                await fresh_member.remove_roles(muted_role, reason="Tự động gỡ mute.")
                if log_channel: await log_channel.send(f"✅ Đã tự động gỡ mute cho {author.mention}.")
                try: await author.send(f"Bạn đã được gỡ mute tại server `{channel.guild.name}`.")
                except discord.Forbidden: pass
                
        except discord.Forbidden:
            if log_channel: await log_channel.send(f"**LỖI:** Bot không có quyền để mute {author.mention}.")
        except Exception as e:
            if log_channel: await log_channel.send(f"**LỖI BẤT NGỜ KHI MUTE:** {e}")

# --- CÁC LỆNH SLASH (/) KHÔNG THAY ĐỔI ---
# (Phần code cho các lệnh /clear, /mute, /unmute và xử lý lỗi giữ nguyên như cũ)

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
    if log_channel:
        embed = discord.Embed(title="Mute thủ công", color=discord.Color.dark_red())
        embed.add_field(name="Thành viên", value=member.mention, inline=False)
        embed.add_field(name="Người thực hiện", value=interaction.user.mention, inline=False)
        embed.add_field(name="Thời gian", value=f"{minutes} phút", inline=False)
        embed.add_field(name="Lý do", value=reason, inline=False)
        await log_channel.send(embed=embed)
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
        await interaction.response.send_message(f"{member.mention} không bị mute hoặc không tìm thấy role Muted.", ephemeral=True)
        return
    await member.remove_roles(muted_role, reason=reason)
    await interaction.response.send_message(f"Đã gỡ mute cho {member.mention}.")
    if log_channel:
        embed = discord.Embed(title="Gỡ mute thủ công", color=discord.Color.green())
        embed.add_field(name="Thành viên", value=member.mention, inline=False)
        embed.add_field(name="Người thực hiện", value=interaction.user.mention, inline=False)
        embed.add_field(name="Lý do", value=reason, inline=False)
        await log_channel.send(embed=embed)

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("Bạn không có quyền để sử dụng lệnh này!", ephemeral=True)
    else:
        print(f"Lỗi lệnh slash không xác định: {error}")
        await interaction.response.send_message("Đã có lỗi xảy ra khi thực hiện lệnh.", ephemeral=True)

# --- CHẠY BOT ---
keep_alive() # Bắt đầu chạy web server
bot.run(TOKEN) # Bắt đầu chạy bot
