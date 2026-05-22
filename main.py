import streamlit as st
import pandas as pd
from rapidfuzz import process, fuzz, utils
import io

# Konfigurasi Halaman Streamlit
st.set_page_config(
    page_title="AI & Exact Customer Mapping",
    page_icon="🤖",
    layout="wide"
)

def load_data(uploaded_file):
    """Fungsi untuk membaca berkas unggahan di Streamlit"""
    try:
        file_name = uploaded_file.name.lower()
        if file_name.endswith(('.xlsx', '.xls')):
            return pd.read_excel(uploaded_file, engine='openpyxl')
        else:
            try:
                return pd.read_csv(uploaded_file, encoding='utf-8')
            except UnicodeDecodeError:
                return pd.read_csv(uploaded_file, encoding='latin1')
    except Exception as e:
        st.error(f"Gagal membaca file {uploaded_file.name}. Terjadi kesalahan: {e}")
        return None

# --- UI Tampilan Utama ---
st.title("🤖 AI Mapping Nasabah (Singkatan & Fullname)")
st.markdown("""
Aplikasi ini memetakan data dengan fitur **Deteksi Singkatan**. 
Contoh: Jika data target bernama **"MUI"**, sistem akan otomatis menerjemahkannya menjadi **"Majelis Ulama Indonesia"** sebelum dicocokkan dengan database internal (mengabaikan huruf besar/kecil).
---
""")

# --- Sidebar Input & Pengaturan ---
st.sidebar.header("⚙️ Pengaturan Parameter")
threshold = st.sidebar.slider(
    "AI Fuzzy Threshold (Minimal Kemiripan %)", 
    min_value=50, 
    max_value=100, 
    value=85, 
    step=1
)

# --- Layout Kolom Upload File ---
col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Database Internal")
    file_db = st.file_uploader("Unggah Database Internal (.xlsx, .csv)", type=['xlsx', 'xls', 'csv'], key="db")

with col2:
    st.subheader("2. Data Target (Bisa Singkatan)")
    file_target = st.file_uploader("Unggah Data Target (.xlsx, .csv)", type=['xlsx', 'xls', 'csv'], key="target")

# --- Eksekusi Utama ---
if file_db and file_target:
    db_internal = load_data(file_db)
    df_target = load_data(file_target)

    if db_internal is not None and df_target is not None:
        st.success("✅ Kedua file berhasil dimuat!")

        if st.button("🚀 Mulai Proses Mapping", type="primary"):
            
            db_internal_clean = db_internal.copy()
            df_target_clean = df_target.copy()

            # Standarisasi Nama Kolom
            db_internal_clean.columns = [str(c).strip().lower() for c in db_internal_clean.columns]
            df_target_clean.columns = [str(c).strip().lower() for c in df_target_clean.columns]
            
            # --- KAMUS SINGKATAN (Tambahkan singkatan lain di sini jika perlu) ---
            kamus_singkatan = {
                "mui": "majelis ulama indonesia",
                "pui": "persatuan umat islam",
                "persis": "persatuan islam",
                "nw": "nahdlatul wathan",
                "wi": "wahdah islamiyyah",
                "ma": "mathla'ul anwar",
                "washliyah": "al jamiyatul washliyah",
                "ii": "al irsyad islamiyah",
                "al": "alkhairat",
                "si": "syarikat islam",
                "ldii": "lembaga dakwah islam indonesia"
            }

            def terjemahkan_singkatan(teks):
                """Fungsi untuk menerjemahkan kata per kata berdasarkan kamus di atas"""
                if not isinstance(teks, str) or teks == "nan": return ""
                # Pecah menjadi kata per kata, ubah ke lowercase
                kata_kata = teks.lower().split()
                # Terjemahkan jika ada di kamus, jika tidak biarkan aslinya
                kata_terjemahan = [kamus_singkatan.get(kata, kata) for kata in kata_kata]
                return " ".join(kata_terjemahan)

            # Daftar GAM
            target_gam_list = [
                "al irsyad islamiyah", "al jamiyatul washliyah", "alkhairat", "majelis ulama indonesia",
                "mathla'ul anwar", "persatuan islam", "persatuan umat islam",
                "syarikat islam", "wahdah islamiyyah", "nahdlatul wathan", "lembaga dakwah islam indonesia"
            ]

            # Menentukan kolom nama di database internal (fallback ke namacif / fullname jika ada)
            col_nama_db = 'namarekening'
            if 'namarekening' not in db_internal_clean.columns:
                if 'fullname' in db_internal_clean.columns:
                    col_nama_db = 'fullname'
                elif 'namacif' in db_internal_clean.columns:
                    col_nama_db = 'namacif'

            # Pembersihan tipe data DB Internal
            if 'norekening' in db_internal_clean.columns:
                db_internal_clean['norekening'] = db_internal_clean['norekening'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
            
            if col_nama_db in db_internal_clean.columns:
                db_internal_clean[col_nama_db] = db_internal_clean[col_nama_db].fillna("").astype(str)
                # Terapkan juga kamus singkatan ke DB Internal (berjaga-jaga jika terbalik)
                db_internal_clean['nama_untuk_ai'] = db_internal_clean[col_nama_db].apply(terjemahkan_singkatan)
            else:
                st.error("❌ Kolom namarekening/namacif/fullname tidak ditemukan di Database Internal.")
                st.stop()
                
            # Kamus untuk Exact Match Rekening
            db_rek_dict = {}
            if 'norekening' in db_internal_clean.columns:
                for idx, val in db_internal_clean['norekening'].items():
                    if val and val != "nan":
                        db_rek_dict[val] = idx

            # List nama DB untuk Fuzzy Match (sudah diterjemahkan jika ada singkatan)
            db_names = db_internal_clean['nama_untuk_ai'].tolist()

            # --- Proses Pencocokan ---
            hasil_status, hasil_nocif, hasil_namacif, hasil_saldosmk, hasil_gam = [], [], [], [], []
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            total_rows = len(df_target_clean)

            for idx, row in df_target_clean.iterrows():
                if idx % 10 == 0 or idx == total_rows - 1:
                    progress_bar.progress((idx + 1) / total_rows)
                    status_text.text(f"Memproses baris data ke-{idx + 1} dari {total_rows}...")

                rek_target = str(row['norekening']).replace(".0", "").strip() if 'norekening' in df_target_clean.columns else ""
                nama_target_asli = str(row['namarekening']).strip() if 'namarekening' in df_target_clean.columns else ""
                gam_target = str(row['gam']).strip().lower() if 'gam' in df_target_clean.columns else ""
                
                # Terjemahkan nama target (MUI -> majelis ulama indonesia)
                nama_target_terjemahan = terjemahkan_singkatan(nama_target_asli)

                match_idx = None
                match_type = None

                # A. Exact Match Rekening
                if rek_target and rek_target != "nan" and rek_target in db_rek_dict:
                    match_idx = db_rek_dict[rek_target]
                    match_type = "EXACT MATCH (REKENING)"
                    
                # B. AI Fuzzy Match
                elif nama_target_terjemahan and nama_target_terjemahan != "":
                    match = process.extractOne(
                        nama_target_terjemahan, 
                        db_names, 
                        scorer=fuzz.token_set_ratio, 
                        processor=utils.default_process
                    )
                    if match and match[1] >= threshold:
                        match_idx = match[2]
                        match_type = f"FUZZY MATCH ({match[1]:.1f}%)"

                # C. Tarik Data
                if match_idx is not None:
                    hasil_status.append(f"NASABAH - {match_type}")
                    hasil_nocif.append(str(db_internal_clean.iloc[match_idx].get('nocif', '')).replace(".0", ""))
                    hasil_namacif.append(db_internal_clean.iloc[match_idx].get('namacif', ''))
                    hasil_saldosmk.append(db_internal_clean.iloc[match_idx].get('saldosmk', ''))
                else:
                    hasil_status.append("BELUM NASABAH")
                    hasil_nocif.append("")
                    hasil_namacif.append("")
                    hasil_saldosmk.append("")

                # D. Cek GAM Target
                if any(g in gam_target or g in nama_target_terjemahan for g in target_gam_list):
                    hasil_gam.append("TERMASUK TARGET GAM")
                else:
                    hasil_gam.append("BUKAN TARGET GAM")

            # --- Simpan ke Dataframe ---
            df_target['Status Nasabah AI'] = hasil_status
            df_target['Hasil_NoCIF'] = hasil_nocif
            df_target['Hasil_NamaCIF'] = hasil_namacif
            df_target['Hasil_SaldoSMK'] = hasil_saldosmk
            df_target['Status GAM'] = hasil_gam

            status_text.empty()
            st.balloons()
            st.success("🎉 Proses Mapping Berhasil Diselesaikan!")

            st.dataframe(df_target.head(10), use_container_width=True)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_target.to_excel(writer, index=False, sheet_name='Hasil Mapping AI')
            processed_data = output.getvalue()

            st.download_button(
                label="📥 Unduh File Excel Hasil Mapping",
                data=processed_data,
                file_name="HASIL_MAPPING_AI_LENGKAP.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
else:
    st.info("💡 Silakan unggah **kedua berkas** untuk memunculkan tombol proses mapping.")
