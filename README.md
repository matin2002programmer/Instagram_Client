# InstagramClient

A powerful and flexible **Instagram Media Downloader & Uploader** built in Python.  
This client allows you to programmatically download, upload, interact with posts, and manage sessions using Instagram’s private web APIs.

---

## 🚀 Features

### 📥 Download
- Posts (single image, video, and carousel)
- Reels
- Stories (by URL or all active stories from a user)
- Highlights
- Full profiles  
  - Profile picture  
  - All posts  
  - Stories  
  - Highlights  

### 📤 Upload
- Upload a **photo** with caption
- Upload a **reel (video)** with optional thumbnail and caption

### 💬 Interactions
- Comment on a post
- Comment on the user’s latest post
- **Like a post**
- **Unlike a post**

### 🔄 Authentication & Session Handling
- Cookie persistence (auto-save & reuse)
- Automatic re-login when required
- Manages Instagram tokens (CSRF, rollout hash, mid, lsd)
- Randomized user-agent to reduce detection risk

### ⚙️ Internal Engine
- Uses `httpx` for all HTTP operations
- Works with both **GraphQL** and **Private REST** endpoints
- Smart filename generation
- Automatic decoding and saving of media files

---

## 📦 Installation

```bash
git clone https://github.com/matin2002programmer/Instagram_Client.git
cd Instagram_Client
pip install -r requirements.txt




💡 Basic Usage


Login


from Instagram_Client import InstagramClient

client = InstagramClient()
client.login("your_username", "your_password")



Download a post


client.download_post("https://www.instagram.com/p/SHORTCODE/")



Download a profile


client.download_user_profile("username_here", max_posts=20)



Upload a photo


client.upload_photo("photo.jpg", caption="Hello World!")



Upload a reel


client.upload_reel("video.mp4", caption="Check this out!")



Comment on a post


client.comment_on_post(
    "https://www.instagram.com/p/SHORTCODE/",
    "Nice post! 🚀"
)



Comment on the user's latest post


client.comment_on_first_post("someuser", "Awesome!")



👍 Like a post


client.like_post("https://www.instagram.com/p/SHORTCODE/")



👎 Unlike a post


client.unlike_post("https://www.instagram.com/p/SHORTCODE/")



Using context manager


with InstagramClient() as client:
    client.login("your_username", "your_password")
    client.download_story("https://www.instagram.com/stories/user/1234567890/")```




⚠️ Notes & Limitations




Instagram private API can change at any time — updates may be required


Automating likes/comments too fast may trigger rate limits


For private profiles, you must be logged in and have access


Large downloads (full profiles) may take time depending on network speed





🛠️ Recommended Improvements (Future Enhancements)


These optimizations would make the repo even stronger:


1. Better Error Handling




Wrap all HTTP actions in try/except


Implement retry with exponential backoff for failed requests


Identify and handle "login required" responses gracefully




2. Type Hinting




Add type hints across classes and methods


Improves maintainability and editor autocompletion




3. Logging System




Use Python’s built-in logging module


Add levels: DEBUG, INFO, WARNING, ERROR


Allow user to enable/disable detailed API logs




4. Rate Limiting




Add optional delay between actions


Avoid accidental spam-like behavior




5. Add Tests




Unit tests for parsing


Integration tests using mocked responses




6. More Documentation


Create:




/docs folder


/examples folder


“How the upload API works” document





📜 License


MIT License — open-source and free to use.



🙏 Support


If you find this project useful, consider starring ⭐ the repo!




---

## 📩 Contact

👨‍💻 Developed by **[Matin](https://www.instagram.com/matindevilish_boy/)**  
📷 Instagram: [@matindevilish_boy](https://www.instagram.com/matindevilish_boy/)  
