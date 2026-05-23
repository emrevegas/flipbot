import json
from pathlib import Path
from typing import Optional, Dict, Any

class Translator:
    def __init__(self, default_lang: str = "en"):
        """
        Çok dilli çeviri sistemi
        
        Args:
            default_lang: Varsayılan dil kodu (tr, en, id)
        """
        self.default_lang = default_lang
        self.lang_folder = Path(__file__).resolve().parents[1] / "database" / "lang"
        self.languages: Dict[str, Dict[str, Any]] = {}
        self.user_languages: Dict[str, str] = {}  # user_id -> lang_code
        
        # Dilleri yükle
        self._load_languages()
        self._load_user_languages()
    
    def _load_languages(self):
        """Tüm dil dosyalarını yükle"""
        lang_codes = ["tr", "en", "id"]
        for lang_code in lang_codes:
            lang_file = self.lang_folder / f"{lang_code}.json"
            if lang_file.exists():
                try:
                    with open(lang_file, "r", encoding="utf-8-sig") as f:
                        self.languages[lang_code] = json.load(f)
                except Exception as e:
                    print(f"Error loading language {lang_code}: {e}")
    
    def _load_user_languages(self):
        """Kullanıcı dil tercihlerini yükle - artık kullanılmıyor, get_user_language dinamik olarak okur"""
        # Deprecated: Her kullanıcı için ayrı dosya kullanılıyor
        self.user_languages = {}
    
    def _save_user_languages(self):
        """Kullanıcı dil tercihlerini kaydet - artık kullanılmıyor"""
        # Deprecated: set_user_language direkt olarak kaydediyor
        pass
    
    def set_user_language(self, user_id: str, lang_code: str):
        """
        Kullanıcının dilini ayarla
        
        Args:
            user_id: Discord kullanıcı ID'si
            lang_code: Dil kodu (tr, en, id)
        """
        if lang_code in self.languages:
            from modules.database import set_user_data
            lang_data = {"language": lang_code}
            set_user_data(int(user_id), "lang", lang_data)
    
    def get_user_language(self, user_id: str) -> str:
        """
        Kullanıcının dilini al (her seferinde dosyadan okur)
        
        Args:
            user_id: Discord kullanıcı ID'si
            
        Returns:
            Dil kodu (tr, en, id)
        """
        # Her çağrıda dosyadan oku (dinamik dil değişikliği için)
        from modules.database import get_user_data
        try:
            lang_data = get_user_data(int(user_id), "lang") or {}
            return lang_data.get("language", self.default_lang)
        except:
            return self.default_lang
    
    def get(self, key: str, user_id: Optional[str] = None, lang: Optional[str] = None, **kwargs) -> str:
        """
        Çeviri al - {} formatında placeholder destekli
        
        Args:
            key: Nokta ile ayrılmış anahtar (örn: "registration.modal_title")
            user_id: Kullanıcı ID'si (opsiyonel)
            lang: Dil kodu (opsiyonel, user_id'den öncelikli)
            **kwargs: Placeholder değerleri
            
        Returns:
            Çevrilmiş metin
            
        Examples:
            >>> t.get("registration.success_title", user_id="123")
            "✅ Kayıt Başarılı!"
            
            >>> t.get("registration.success_description", lang="en", name="John", age="25")
            "Welcome John! Your account has been created..."
        """
        # Dil kodunu belirle
        if lang and lang in self.languages:
            lang_code = lang
        elif user_id:
            lang_code = self.get_user_language(user_id)
        else:
            lang_code = self.default_lang
        
        # Dil verisini al
        lang_data = self.languages.get(lang_code, self.languages.get(self.default_lang, {}))
        
        # Anahtarı parçala ve değeri bul
        keys = key.split(".")
        value = lang_data
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return f"[Missing: {key}]"
        
        # Placeholder'ları değiştir
        if kwargs and isinstance(value, str):
            try:
                value = value.format(**kwargs)
            except KeyError as e:
                print(f"Missing placeholder {e} in key {key}")
        
        return value
    
    def get_all_languages(self) -> list:
        """Tüm mevcut dilleri al"""
        return list(self.languages.keys())


# Global translator instance
translator = Translator(default_lang="en")


def t(key: str, user_id: Optional[str] = None, lang: Optional[str] = None, **kwargs) -> str:
    """
    Kısayol çeviri fonksiyonu
    
    Args:
        key: Çeviri anahtarı
        user_id: Kullanıcı ID'si
        lang: Dil kodu
        **kwargs: Placeholder değerleri
        
    Returns:
        Çevrilmiş metin
    """
    return translator.get(key, user_id=user_id, lang=lang, **kwargs)
