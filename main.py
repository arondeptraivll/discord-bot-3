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
# ID kênh để ghi log
LOG_CHANNEL_ID = 123456789012345678  # << THAY ID KÊNH LOG CỦA BẠN VÀO ĐÂY
# Tên role "Muted"
MUTED_ROLE_NAME = "Muted" 

# Cấu hình chống spam - BẠN CÓ THỂ CHỈNH CÁC THÔNG SỐ NÀY
SPAM_CONFIG = {
    'rate_limit_count': 5,  # Số tin nhắn để tính là spam nhanh
    'rate_limit_seconds': 3, # Trong khoảng thời gian (giây)
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

# Cấu trúc dữ liệu để theo dõi hành vi của người dùng
# defaultdict giúp không cần kiểm tra key tồn tại hay không
# deque là một hàng đợi hiệu quả để lưu các tin nhắn/thời gian gần nhất
user_spam_data = defaultdict(lambda: {
    'message_timestamps': deque(maxlen=SPAM_CONFIG['rate_limit_count']),
    'last_messages': deque(maxlen=SPAM_CONFIG['duplicate_count']),
    'warnings': 0
})

# Nhập hàm keep_alive từ file kia
from keep_alive import keep_alive

# --- SỰ KIỆN BOT ---
@bot.event
async def on_ready():
    print(f'Bot đã đăng nhập với tên {bot.user}')
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
    # 1. Bỏ qua nếu tin nhắn từ chính bot
    if message.author == bot.user:
        return
    # 2. Bỏ qua nếu là tin nhắn riêng (DM)
    if not message.guild:
        return
    # 3. Miễn trừ cho quản trị viên/người có quyền quản lý tin nhắn
    if message.author.guild_permissions.manage_messages:
        return

    author_id = message.author.id
    user_data = user_spam_data[author_id]
    current_time = datetime.datetime.now(datetime.timezone.utc)
    
    # --- LOGIC CHỐNG SPAM TINH VI ---
    
    # 1. KIỂM TRA SPAM TIN NHẮN NHANH (RATE LIMITING)
    user_data['message_timestamps'].append(current_time)
    if len(user_data['message_timestamps']) == SPAM_CONFIG['rate_limit_count']:
        time_diff = (user_data['message_timestamps'][-1] - user_data['message_timestamps'][0]).total_seconds()
        if time_diff < SPAM_CONFIG['rate_limit_seconds']:
            await handle_spam(message, "Spam tin nhắn quá nhanh")
            return # Dừng xử lý thêm

    # 2. KIỂM TRA SPAM TIN NHẮN TRÙNG LẶP
    user_data['last_messages'].append(message.content)
    if len(user_data['last_messages']) == SPAM_CONFIG['duplicate_count']:
        # Kiểm tra xem tất cả các tin nhắn trong deque có giống nhau không
        if len(set(user_data['last_messages'])) == 1:
            await handle_spam(message, "Spam tin nhắn trùng lặp")
            return
    
    # 3. KIỂM TRA SPAM ĐỀ CẬP (MENTION SPAM)
    if len(message.mentions) + len(message.role_mentions) > SPAM_CONFIG['max_mentions']:
        await handle_spam(message, "Spam đề cập (mention)")
        return
    
    # 4. KIỂM TRA SPAM CHỮ IN HOA (CAPS SPAM)
    content = message.content
    if len(content) > SPAM_CONFIG['min_caps_length']:
        # Đếm số chữ cái in hoa
        uppercase_chars = sum(1 for char in content if char.isupper())
        # Đếm tổng số chữ cái
        alpha_chars = sum(1 for char in content if char.isalpha())
        if alpha_chars > 0 and (uppercase_chars / alpha_chars) > SPAM_CONFIG['caps_ratio']:
            await handle_spam(message, f"Gửi tin nhắn có tỷ lệ chữ IN HOA quá cao (>{int(SPAM_CONFIG['caps_ratio']*100)}%)")
            return

    # Lệnh !hello để bot xử lý lệnh cũ (nếu bạn muốn)
    await bot.process_commands(message)

# --- HÀM HỖ TRỢ XỬ LÝ SPAM ---
async def handle_spam(message, reason):
    """Hàm trung tâm để xử lý các hành vi spam."""
    author = message.author
    author_id = author.id
    user_data = user_spam_data[author_id]
    user_data['warnings'] += 1
    
    # Xóa tin nhắn vi phạm
    try:
        await message.delete()
    except discord.NotFound:
        pass # Tin nhắn có thể đã bị xóa bởi người khác

    warning_count = user_data['warnings']
    
    # Tạo embed để thông báo
    embed = discord.Embed(
        title="⚠️ Phát Hiện Hành Vi Spam ⚠️",
        description=f"**Người dùng:** {author.mention}\n**Lý do:** {reason}",
        color=discord.Color.orange()
    )
    embed.add_field(name="Cảnh cáo lần thứ", value=f"{warning_count}/{SPAM_CONFIG['warning_limit']}", inline=True)
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    
    # Gửi cảnh báo tới kênh log
    if log_channel:
        await log_channel.send(embed=embed)
        
    # Gửi tin nhắn cảnh cáo riêng cho người dùng
    try:
        await author.send(
            f"Bạn đã bị cảnh cáo trong server `{message.guild.name}` vì lý do: **{reason}**.\n"
            f"Đây là cảnh cáo thứ **{warning_count}/{SPAM_CONFIG['warning_limit']}**. "
            f"Nếu bạn tiếp tục vi phạm, bạn sẽ bị câm lặng tạm thời."
        )
    except discord.Forbidden:
        # Không thể gửi tin nhắn riêng, có thể người dùng đã khóa DM
        pass
        
    # Nếu đạt đến giới hạn cảnh cáo, tự động MUTE
    if warning_count >= SPAM_CONFIG['warning_limit']:
        # Reset cảnh cáo sau khi mute
        user_data['warnings'] = 0 
        
        # Tìm role Muted
        muted_role = discord.utils.get(message.guild.roles, name=MUTED_ROLE_NAME)
        if not muted_role:
            if log_channel:
                await log_channel.send(f"**LỖI:** Không tìm thấy role `{MUTED_ROLE_NAME}`. Không thể mute người dùng.")
            return

        try:
            # Áp dụng mute
            await author.add_roles(muted_role, reason="Tự động mute do spam liên tục.")
            
            # Thông báo trong kênh log
            mute_embed = discord.Embed(
                title="🚫 Tự Động Mute 🚫",
                description=f"**Người dùng:** {author.mention} đã bị câm lặng.\n**Thời gian:** {SPAM_CONFIG['mute_duration_minutes']} phút.",
                color=discord.Color.red()
            )
            mute_embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            if log_channel:
                await log_channel.send(embed=mute_embed)

            # Gửi thông báo cho người dùng
            await author.send(f"Bạn đã bị câm lặng tạm thời trong **{SPAM_CONFIG['mute_duration_minutes']} phút** tại server `{message.guild.name}` do spam liên tục.")
            
            # Lên lịch unmute
            await asyncio.sleep(SPAM_CONFIG['mute_duration_minutes'] * 60)
            
            # Kiểm tra xem người dùng còn bị mute không trước khi gỡ
            if muted_role in author.roles:
                await author.remove_roles(muted_role, reason="Tự động gỡ mute.")
                if log_channel:
                    await log_channel.send(f"✅ Đã tự động gỡ mute cho {author.mention}.")
                await author.send(f"Bạn đã được gỡ mute tại server `{message.guild.name}`.")
                
        except discord.Forbidden:
            if log_channel:
                await log_channel.send(f"**LỖI:** Bot không có quyền để mute {author.mention}.")
        except Exception as e:
            if log_channel:
                await log_channel.send(f"**LỖI BẤT NGỜ KHI MUTE:** {e}")

# --- LỆNH SLASH (/) ---

@bot.tree.command(name="clear", description="Xóa một số lượng tin nhắn nhất định.")
@app_commands.describe(amount="Số lượng tin nhắn cần xóa (tối đa 100).")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    if amount <= 0 or amount > 100:
        await interaction.response.send_message("Vui lòng nhập một số từ 1 đến 100.", ephemeral=True)
        return
        
    await interaction.response.defer(ephemeral=True) # Thông báo bot đang xử lý
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Đã xóa thành công {len(deleted)} tin nhắn.", ephemeral=True)

@bot.tree.command(name="mute", description="Câm lặng một thành viên trong một khoảng thời gian.")
@app_commands.describe(member="Thành viên cần mute", minutes="Số phút muốn mute", reason="Lý do mute")
@app_commands.checks.has_permissions(manage_roles=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "Không có lý do"):
    if member == interaction.user:
        await interaction.response.send_message("Bạn không thể tự mute mình!", ephemeral=True)
        return
    if member.guild_permissions.administrator:
        await interaction.response.send_message("Bạn không thể mute Quản trị viên!", ephemeral=True)
        return
    
    muted_role = discord.utils.get(interaction.guild.roles, name=MUTED_ROLE_NAME)
    if not muted_role:
        await interaction.response.send_message(f"Không tìm thấy role `{MUTED_ROLE_NAME}`! Vui lòng tạo role này trước.", ephemeral=True)
        return
        
    await member.add_roles(muted_role, reason=reason)
    await interaction.response.send_message(f"Đã mute {member.mention} trong {minutes} phút. Lý do: {reason}")
    
    # Gửi log
    if log_channel:
        embed = discord.Embed(title="Mute thủ công", color=discord.Color.dark_red())
        embed.add_field(name="Thành viên", value=member.mention, inline=False)
        embed.add_field(name="Người thực hiện", value=interaction.user.mention, inline=False)
        embed.add_field(name="Thời gian", value=f"{minutes} phút", inline=False)
        embed.add_field(name="Lý do", value=reason, inline=False)
        await log_channel.send(embed=embed)
        
    await asyncio.sleep(minutes * 60)
    
    # Gỡ mute sau khi hết thời gian
    if muted_role in member.roles:
        await member.remove_roles(muted_role, reason="Hết thời gian mute.")
        if log_channel:
            await log_channel.send(f"Đã tự động gỡ mute cho {member.mention} sau khi hết hạn.")

@bot.tree.command(name="unmute", description="Gỡ câm lặng cho một thành viên.")
@app_commands.describe(member="Thành viên cần gỡ mute", reason="Lý do gỡ mute")
@app_commands.checks.has_permissions(manage_roles=True)
async def unmute(interaction: discord.Interaction, member: discord.Member, reason: str = "Gỡ mute thủ công"):
    muted_role = discord.utils.get(interaction.guild.roles, name=MUTED_ROLE_NAME)
    if not muted_role:
        await interaction.response.send_message(f"Không tìm thấy role `{MUTED_ROLE_NAME}`!", ephemeral=True)
        return
    
    if muted_role not in member.roles:
        await interaction.response.send_message(f"{member.mention} không bị mute.", ephemeral=True)
        return
        
    await member.remove_roles(muted_role, reason=reason)
    await interaction.response.send_message(f"Đã gỡ mute cho {member.mention}.")
    
    # Gửi log
    if log_channel:
        embed = discord.Embed(title="Gỡ mute thủ công", color=discord.Color.green())
        embed.add_field(name="Thành viên", value=member.mention, inline=False)
        embed.add_field(name="Người thực hiện", value=interaction.user.mention, inline=False)
        embed.add_field(name="Lý do", value=reason, inline=False)
        await log_channel.send(embed=embed)

# Xử lý lỗi cho các lệnh không có quyền
@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("Bạn không có quyền để sử dụng lệnh này!", ephemeral=True)
    else:
        # Gửi lỗi khác vào console để debug
        raise error

# --- CHẠY BOT ---
keep_alive() # Bắt đầu chạy web server
bot.run(TOKEN) # Bắt đầu chạy bot
