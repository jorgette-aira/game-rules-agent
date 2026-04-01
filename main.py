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
sessions_col = db["sessions"]  # Permanent Memory Collection

# Create a text index in MongoDB for faster searching (Run this once)
collection.create_index([("content", "text")])

# ---------------------------------------------------------
# 2. MEMORY HELPER FUNCTIONS
# ---------------------------------------------------------
def get_user_memory(chat_id):
    """Retrieve chat history from MongoDB."""
    user_data = sessions_col.find_one({"chat_id": chat_id})
    if user_data:
        return user_data.get("history", [])
    return []

def save_user_memory(chat_id, history):
    """Save the last 10 messages of history to MongoDB."""
    limited_history = history[-10:]
    sessions_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"history": limited_history}},
        upsert=True
    )

# ---------------------------------------------------------
# 3. OPENAI VISION (OCR)
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
# 4. CLOUD DATA INGESTION
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

if collection.count_documents({}) == 0:
    print("\n[!] Cloud Database is empty. Scanning rulebooks...")
    pdf_files = glob.glob("rulebooks/*.pdf")
    for pdf in pdf_files:
        load_pdf_to_db(pdf)
else:
    print(f"\n[!] Cloud Brain active! Found {collection.count_documents({})} rule chunks.")

# ---------------------------------------------------------
# 5. CHAT AGENT SETUP
# ---------------------------------------------------------
system_prompt = (
    "You are Cj, a friendly and high-energy Game Master. Your tone is "
    "approachable, energetic, and uses natural Taglish. "
    "Personality: Use polite but fun expressions like 'Game!', 'Check natin...', "
    "'Copy that!', or 'Ready ka na?' to keep the vibe light and engaging. "
    
    "CRITICAL RULE: You must ONLY use the provided 'Game Rules Context' for facts. "
    "If a rule isn't there, politely explain that it's not in the manual "
    "(e.g., 'Pasensya na, pero wala yan sa official manual natin!'). "
    
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
            
            # Save the specific game context into permanent memory
            new_history = [{"role": "assistant", "content": f"Context: We are now playing {game_name}."}]
            save_user_memory(chat_id, new_history)

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
            current_history = get_user_memory(chat_id)
            current_history.append({"role": "assistant", "content": f"Context: The user is asking about {detected_game}."})
            save_user_memory(chat_id, current_history)
            
    except Exception as e:
        print(f"Router Error (Handled): {e}")

    # 3. RAG QUESTION PROCESS
    bot.send_chat_action(chat_id, 'typing')
    try:
        # Load permanent memory from MongoDB
        history = get_user_memory(chat_id)
        
        # MongoDB Keyword/Text Search
        search_query = f"{detected_game} {user_text}" if detected_game != "None" else user_text
        cursor = collection.find({"$text": {"$search": search_query}}).limit(15)
        results = list(cursor)
        
        context = "\n\n".join([r["content"] for r in results]) if results else "No rules found."
        
        # Build message sequence with permanent history
        messages = [{"role": "system", "content": system_prompt}] + history
        messages.append({"role": "user", "content": f"Question: {user_text}\n\nGame Rules Context:\n{context}"})
        
        res = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.2)
        
        # Clean response format
        raw_answer = res.choices[0].message.content
        answer = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', raw_answer).replace('*', '')

        print(f"\n================== CJ'S RESPONSE ==================\n{answer}\n===================================================\n")
        
        # Update and Save History to MongoDB
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": answer})
        save_user_memory(chat_id, history)
        
        bot.reply_to(message, answer, parse_mode='HTML')
        
    except Exception as e:
        if "content_filter" in str(e).lower():
            bot.reply_to(message, "Pasensya na lods, pero restricted topic yan! Ask about games na lang tayo. 😉")
        else:
            bot.reply_to(message, "Pasensya na, the GM hit a snag!")
        print(f"Error: {e}")

print("\nConnecting to Telegram...")
bot.infinity_polling(skip_pending=True)