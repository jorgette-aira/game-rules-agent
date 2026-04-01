import os
import glob
import telebot
import fitz  # PyMuPDF
import base64
import re
import pymongo
import certifi
from telebot import types
from PyPDF2 import PdfReader
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ---------------------------------------------------------
# 1. API KEYS & CLOUD SETUP
# ---------------------------------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

# Initialize OpenAI Client
client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN
)

# Connect to MongoDB Atlas
mongo_client = pymongo.MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = mongo_client["game_master_db"]
collection = db["game_rules"]

# Create a text index in MongoDB for faster searching (Run this once)
collection.create_index([("content", "text")])

# ---------------------------------------------------------
# 2. OPENAI VISION (OCR)
# ---------------------------------------------------------
def extract_text_with_openai_vision(pdf_path):
    print(f"\n[!] Engaging OpenAI Vision for {pdf_path}...")
    doc = fitz.open(pdf_path)
    full_text = ""
    for page_num in range(len(doc)):
        print(f"Scanning page {page_num + 1} of {len(doc)}...")
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        base64_image = base64.b64encode(img_data).decode('utf-8')
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "You are an OCR engine. Extract all readable text from this board game rulebook page. Do not transcribe images, just the exact text."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                    ]
                }]
            )
            if response.choices[0].message.content:
                full_text += response.choices[0].message.content + "\n"
        except Exception as e:
            print(f"  -> Error reading page {page_num + 1}: {e}")
    return full_text

# ---------------------------------------------------------
# 3. CLOUD DATA INGESTION
# ---------------------------------------------------------
def load_pdf_to_db(pdf_path: str):
    print(f"Cloud Ingesting {pdf_path}...")
    reader = PdfReader(pdf_path)
    game_name = os.path.basename(pdf_path).replace(".pdf", "")
    full_text = ""
    
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"
            
    if len(full_text.strip()) < 100:
        print("  -> No text layer found. Switching to Visual OCR...")
        full_text = extract_text_with_openai_vision(pdf_path)
    
    chunk_size = 800
    start = 0
    docs_to_insert = []
    
    while start < len(full_text):
        end = start + chunk_size
        chunk_content = full_text[start:end]
        
        docs_to_insert.append({
            "game": game_name,
            "content": f"RULES FOR {game_name.upper()} GAME: {chunk_content}",
            "metadata": {"source": pdf_path}
        })
        start += chunk_size - 100 
        
    if docs_to_insert:
        collection.insert_many(docs_to_insert)
        print(f"Successfully uploaded {len(docs_to_insert)} chunks for {game_name} to the cloud!\n")

# Check if Cloud DB is empty
if collection.count_documents({}) == 0:
    print("\n[!] Cloud Database is empty. Scanning rulebooks...")
    pdf_files = glob.glob("rulebooks/*.pdf")
    for pdf in pdf_files:
        load_pdf_to_db(pdf)
else:
    print(f"\n[!] Cloud Brain active! Found {collection.count_documents({})} rule chunks.")

# ---------------------------------------------------------
# 4. CHAT AGENT SETUP
# ---------------------------------------------------------
system_prompt = (
    "You are Cj, the ultimate Game Master barkada. Your tone is energetic, "
    "witty, and very 'Taglish' (natural mix of Tagalog and English). "
    "Personality: Use expressions like 'G!', 'Lods', 'Check natin...', or 'GG!' "
    "to make it feel like a real conversation. "
    
    "CRITICAL RULE: You must ONLY use the provided 'Game Rules Context' for facts. "
    "If a rule isn't there, say it straight but with style (e.g., 'Hala, wala sa manual yan lods!'). "
    
    "POLITENESS EXCEPTION: Be friendly! You can greet, say thanks, and use emojis "
    "without checking the rules. "
    
    "STRICT FORMATTING: "
    "1. NEVER use asterisks (*). "
    "2. Use <b>text</b> for bold. "
    "3. Use <i>text</i> for italics. "
    "4. Use dashes (-) or bullets (•) for lists. "
    "5. Keep it short, punchy, and natural. No robotic paragraphs!"
)

bot = telebot.TeleBot(TG_TOKEN)
user_memory = {}
MAX_HISTORY = 4

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    try:
        responses = {
            "btn_uno": ("Uno No Mercy", "What's the situation? May nag-plus 10 ba?"),
            "btn_werewolf": ("One Night Ultimate Werewolf", "Who's acting sus? Ask me about roles!"),
            "btn_kittens": ("Exploding Kittens", "Ready for the defuse? Ask about bombing!"),
            "btn_organ": ("Organ Attack", "Time to spread some diseases! Which organ are we hitting?"),
            "btn_monopoly": ("Monopoly", "Ready to bankrupt friends? Ask about rent or hotels!")
        }
        
        if call.data in responses:
            game_name, msg_text = responses[call.data]
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, f"<b>{game_name}</b> is active! {msg_text}", parse_mode='HTML')
            user_memory[chat_id] = [{"role": "assistant", "content": f"Context: We are now playing {game_name}."}]
    except Exception as e:
        print(f"[!] Callback error: {e}")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    chat_id = message.chat.id
    user_text = message.text
    text_lower = user_text.lower() if user_text else ""
    
    # 1. GREETING CHECK
    greetings = ['hello', 'hi', 'hey', 'start', 'uuy', 'halo', 'thanks', 'thank you', 'salamat']
    if text_lower.startswith('/') or any(g in text_lower for g in greetings):
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn1 = types.InlineKeyboardButton("🃏 Uno No Mercy", callback_data="btn_uno")
        btn2 = types.InlineKeyboardButton("🐺 Werewolf", callback_data="btn_werewolf")
        btn3 = types.InlineKeyboardButton("😺 Exploding Kittens", callback_data="btn_kittens")
        btn4 = types.InlineKeyboardButton("🏥 Organ Attack", callback_data="btn_organ")
        btn5 = types.InlineKeyboardButton("💰 Monopoly", callback_data="btn_monopoly")
        markup.add(btn1, btn2, btn3, btn4, btn5)
        bot.send_message(chat_id, "<b>Hiiii! I'm Cj!</b> 🎮\nReady ka na? Click a game or ask away!", reply_markup=markup, parse_mode='HTML')
        return

    # 2. AUTOMATIC GAME DETECTION (The Router)
    print(f"[!] Detecting game intent for: {user_text}")
    game_list = "Uno No Mercy, One Night Ultimate Werewolf, Exploding Kittens, Organ Attack, Monopoly"
    detected_game = "None" 
    
    try:
        detect_res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system", 
                "content": f"You are a category classifier. Identify if the user is talking about: [{game_list}]. Return ONLY the game name or 'None'. Be safe."
            },
            {"role": "user", "content": user_text}]
        )
        
        if detect_res.choices[0].message.content:
            detected_game = detect_res.choices[0].message.content.strip()

        if detected_game != "None":
            print(f"--- Detected Game: {detected_game} ---")
            if chat_id not in user_memory: user_memory[chat_id] = []
            user_memory[chat_id].append({"role": "assistant", "content": f"Context: The user is asking about {detected_game}."})
            
    except Exception as e:
        print(f"Router Error (Handled): {e}")

    # 3. RAG QUESTION PROCESS
    bot.send_chat_action(chat_id, 'typing')
    try:
        # MongoDB Keyword/Text Search
        search_query = f"{detected_game} {user_text}" if detected_game != "None" else user_text
        
        # Searching MongoDB using Text Index
        cursor = collection.find({"$text": {"$search": search_query}}).limit(15)
        results = list(cursor)
        
        context = "\n\n".join([r["content"] for r in results]) if results else "No rules found."
        
        if chat_id not in user_memory: user_memory[chat_id] = []
        messages = [{"role": "system", "content": system_prompt}] + user_memory[chat_id]
        messages.append({"role": "user", "content": f"Question: {user_text}\n\nGame Rules Context:\n{context}"})
        
        res = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.2)
        answer = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', res.choices[0].message.content).replace('*', '')

        print(f"\n================== CJ'S RESPONSE ==================\n{answer}\n===================================================\n")
        
        user_memory[chat_id].append({"role": "user", "content": user_text})
        user_memory[chat_id].append({"role": "assistant", "content": answer})
        user_memory[chat_id] = user_memory[chat_id][-MAX_HISTORY:]
        
        bot.reply_to(message, answer, parse_mode='HTML')
        
    except Exception as e:
        if "content_filter" in str(e).lower():
            bot.reply_to(message, "Uy lods, bawal yan! AI says too spicy yung topic. Try mo rephrase! 😉")
        else:
            bot.reply_to(message, "Pasensya na, the GM hit a snag!")
        print(f"Error: {e}")

print("\nConnecting to Telegram...")
bot.infinity_polling(skip_pending=True)