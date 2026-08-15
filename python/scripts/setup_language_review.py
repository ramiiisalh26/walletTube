"""
1. Creates the video_language_review table for flagged transcripts.
2. Seeds all ISO 639-1 world language codes into the languages table.
3. Flags existing transcripts with suspicious languages (nn, und, etc.).

Run: python -m scripts.setup_language_review
"""
import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal


# All ISO 639-1 language codes  (code, name)
_LANGUAGES = [
    ("af", "Afrikaans"), ("ak", "Akan"), ("sq", "Albanian"), ("am", "Amharic"),
    ("ar", "Arabic"), ("an", "Aragonese"), ("hy", "Armenian"), ("as", "Assamese"),
    ("av", "Avaric"), ("ay", "Aymara"), ("az", "Azerbaijani"), ("bm", "Bambara"),
    ("ba", "Bashkir"), ("eu", "Basque"), ("be", "Belarusian"), ("bn", "Bengali"),
    ("bi", "Bislama"), ("bs", "Bosnian"), ("br", "Breton"), ("bg", "Bulgarian"),
    ("my", "Burmese"), ("ca", "Catalan"), ("ch", "Chamorro"), ("ce", "Chechen"),
    ("ny", "Chichewa"), ("zh", "Chinese"), ("cv", "Chuvash"), ("kw", "Cornish"),
    ("co", "Corsican"), ("cr", "Cree"), ("hr", "Croatian"), ("cs", "Czech"),
    ("da", "Danish"), ("dv", "Divehi"), ("nl", "Dutch"), ("dz", "Dzongkha"),
    ("en", "English"), ("eo", "Esperanto"), ("et", "Estonian"), ("ee", "Ewe"),
    ("fo", "Faroese"), ("fj", "Fijian"), ("fi", "Finnish"), ("fr", "French"),
    ("fy", "Western Frisian"), ("ff", "Fula"), ("gd", "Scottish Gaelic"),
    ("gl", "Galician"), ("lg", "Ganda"), ("ka", "Georgian"), ("de", "German"),
    ("el", "Greek"), ("kl", "Greenlandic"), ("gn", "Guarani"), ("gu", "Gujarati"),
    ("ht", "Haitian Creole"), ("ha", "Hausa"), ("he", "Hebrew"), ("hz", "Herero"),
    ("hi", "Hindi"), ("ho", "Hiri Motu"), ("hu", "Hungarian"), ("ia", "Interlingua"),
    ("id", "Indonesian"), ("ga", "Irish"), ("ig", "Igbo"), ("ik", "Inupiaq"),
    ("io", "Ido"), ("is", "Icelandic"), ("it", "Italian"), ("iu", "Inuktitut"),
    ("ja", "Japanese"), ("jv", "Javanese"), ("kn", "Kannada"), ("kr", "Kanuri"),
    ("ks", "Kashmiri"), ("kk", "Kazakh"), ("km", "Khmer"), ("ki", "Kikuyu"),
    ("rw", "Kinyarwanda"), ("ky", "Kyrgyz"), ("kv", "Komi"), ("kg", "Kongo"),
    ("ko", "Korean"), ("ku", "Kurdish"), ("lo", "Lao"), ("la", "Latin"),
    ("lv", "Latvian"), ("li", "Limburgish"), ("ln", "Lingala"), ("lt", "Lithuanian"),
    ("lu", "Luba-Katanga"), ("lb", "Luxembourgish"), ("mk", "Macedonian"),
    ("mg", "Malagasy"), ("ms", "Malay"), ("ml", "Malayalam"), ("mt", "Maltese"),
    ("gv", "Manx"), ("mi", "Maori"), ("mr", "Marathi"), ("mh", "Marshallese"),
    ("mn", "Mongolian"), ("na", "Nauru"), ("nv", "Navajo"), ("nd", "North Ndebele"),
    ("nr", "South Ndebele"), ("ng", "Ndonga"), ("ne", "Nepali"), ("no", "Norwegian"),
    ("nb", "Norwegian Bokmal"), ("nn", "Norwegian Nynorsk"), ("oc", "Occitan"),
    ("oj", "Ojibwe"), ("or", "Oriya"), ("om", "Oromo"), ("os", "Ossetic"),
    ("pi", "Pali"), ("ps", "Pashto"), ("fa", "Persian"), ("pl", "Polish"),
    ("pt", "Portuguese"), ("pa", "Punjabi"), ("qu", "Quechua"), ("ro", "Romanian"),
    ("rm", "Romansh"), ("rn", "Kirundi"), ("ru", "Russian"), ("se", "Northern Sami"),
    ("sm", "Samoan"), ("sg", "Sango"), ("sa", "Sanskrit"), ("sc", "Sardinian"),
    ("sr", "Serbian"), ("sn", "Shona"), ("sd", "Sindhi"), ("si", "Sinhala"),
    ("sk", "Slovak"), ("sl", "Slovenian"), ("so", "Somali"), ("st", "Southern Sotho"),
    ("es", "Spanish"), ("su", "Sundanese"), ("sw", "Swahili"), ("ss", "Swati"),
    ("sv", "Swedish"), ("tl", "Tagalog"), ("ty", "Tahitian"), ("tg", "Tajik"),
    ("ta", "Tamil"), ("tt", "Tatar"), ("te", "Telugu"), ("th", "Thai"),
    ("bo", "Tibetan"), ("ti", "Tigrinya"), ("to", "Tonga"), ("ts", "Tsonga"),
    ("tn", "Tswana"), ("tr", "Turkish"), ("tk", "Turkmen"), ("tw", "Twi"),
    ("ug", "Uyghur"), ("uk", "Ukrainian"), ("ur", "Urdu"), ("uz", "Uzbek"),
    ("ve", "Venda"), ("vi", "Vietnamese"), ("vo", "Volapuk"), ("wa", "Walloon"),
    ("cy", "Welsh"), ("wo", "Wolof"), ("xh", "Xhosa"), ("yi", "Yiddish"),
    ("yo", "Yoruba"), ("za", "Zhuang"), ("zu", "Zulu"),
    # Extra codes Whisper uses
    ("und", "Undetermined"), ("xx", "Unknown"),
]

# Language codes Whisper often misdetects when audio has little/no speech
_SUSPICIOUS_CODES = {
    "nn", "nb", "fo", "is", "gd", "cy", "ga", "gv", "kw", "br",
    "la", "sa", "pi", "cu", "und", "xx",
}


async def main():
    async with AsyncSessionLocal() as session:

        # ── 1. Create video_language_review table ─────────────────────────────
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS video_language_review (
                id              SERIAL PRIMARY KEY,
                video_id        INTEGER REFERENCES videos(id) ON DELETE CASCADE,
                transcript_id   INTEGER REFERENCES transcripts(id) ON DELETE SET NULL,
                detected_lang   VARCHAR(10)  NOT NULL,
                segment_count   INTEGER,
                reason          VARCHAR(100) NOT NULL DEFAULT 'suspicious_language',
                status          VARCHAR(20)  NOT NULL DEFAULT 'pending',
                notes           TEXT,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                CONSTRAINT video_language_review_status_check
                    CHECK (status IN ('pending','reviewed','corrected','ignored'))
            )
        """))
        await session.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_vlr_video_id ON video_language_review(video_id)"
        ))
        await session.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_vlr_status ON video_language_review(status)"
        ))
        await session.commit()
        print("video_language_review table ready")

        # ── 2. Seed all world language codes ──────────────────────────────────
        inserted = 0
        for code, name in _LANGUAGES:
            result = await session.execute(
                text("SELECT id FROM languages WHERE code = :c"),
                {"c": code},
            )
            if result.fetchone() is None:
                await session.execute(
                    text("INSERT INTO languages (code, name, is_active) VALUES (:c, :n, TRUE)"),
                    {"c": code, "n": name},
                )
                inserted += 1
        await session.commit()
        print(f"Languages seeded: {inserted} new added ({len(_LANGUAGES)} total defined)")

        # ── 3. Flag existing transcripts with suspicious language codes ────────
        flagged = 0
        rows = (await session.execute(text("""
            SELECT t.id AS transcript_id, t.video_id, l.code AS lang,
                   (SELECT COUNT(*) FROM transcript_chunks tc WHERE tc.transcript_id = t.id)
                       AS segment_count
            FROM transcripts t
            JOIN languages l ON l.id = t.language_id
            WHERE l.code = ANY(:codes)
              AND t.source = 'whisper'
              AND NOT EXISTS (
                  SELECT 1 FROM video_language_review vlr WHERE vlr.transcript_id = t.id
              )
        """), {"codes": list(_SUSPICIOUS_CODES)})).fetchall()

        for r in rows:
            reason = "low_segments" if (r.segment_count or 0) < 20 else "suspicious_language"
            await session.execute(text("""
                INSERT INTO video_language_review
                    (video_id, transcript_id, detected_lang, segment_count, reason)
                VALUES (:vid, :tid, :lang, :segs, :reason)
            """), {
                "vid": r.video_id,
                "tid": r.transcript_id,
                "lang": r.lang,
                "segs": r.segment_count,
                "reason": reason,
            })
            flagged += 1

        await session.commit()
        print(f"Flagged {flagged} transcripts for review (suspicious language or low segments)")
        print("\nDone. View flagged videos:")
        print("  SELECT vlr.*, v.title, v.youtube_video_id")
        print("  FROM video_language_review vlr")
        print("  JOIN videos v ON v.id = vlr.video_id")
        print("  WHERE vlr.status = 'pending' ORDER BY vlr.created_at DESC;")


asyncio.run(main())
