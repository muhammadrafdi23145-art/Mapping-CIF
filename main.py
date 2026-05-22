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
    """Fungsi untuk membaca berkas unggahan (Excel/CSV) di Streamlit"""
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
st.title("🤖 AI & Exact Mapping Nasabah")
st.markdown("""
Aplikasi ini memetakan data prospek dengan database internal bank menggunakan metode **Hybrid Matching**:
1. **Exact Match** berdasarkan Nomor Rekening (Akurasi 100%).
2. **AI Fuzzy Match** berdasarkan Nama Rekening (Menghitung kemiripan kata jika nomor rekening kosong/tidak cocok).
---
""")

# --- Sidebar Input & Pengaturan ---
st.sidebar.header("⚙️ Pengaturan Parameter")
threshold = st.sidebar.slider(
    "AI Fuzzy Threshold (Minimal Kemiripan %)", 
    min_value=50, 
    max_value=100, 
    value=85, 
    step=1,
    help="Semakin tinggi nilai threshold, pencocokan nama akan semakin ketat."
)

st.sidebar.markdown("---")
st.sidebar.info("""
**Daftar Target GAM Berdasarkan Sistem:**
* Al Irsyad Islamiyah, Al Jamiyatul Washliyah, Alkhairat, MUI, Mathla'ul Anwar, Persatuan Islam, Persatuan Umat Islam, Syarikat Islam, Wahdah Islamiyyah, Nahdlatul Wathan.
""")

# --- Layout Kolom Upload File ---
col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Database Internal")
    file_db = st.file_uploader("Unggah Database Internal Bank (.xlsx, .xls, .csv)", type=['xlsx', 'xls', 'csv'], key="db")

with col2:
    st.subheader("2. Data yang Dicari (Target)")
    file_target = st.file_uploader("Unggah Data Target Prospek (.xlsx, .xls, .csv)", type=['xlsx', 'xls', 'csv'], key="target")

# --- Eksekusi Utama ---
if file_db and file_target:
    db_internal = load_data(file_db)
    df_target = load_data(file_target)

    if db_internal is not None and df_target is not None:
        st.success("✅ Kedua file berhasil dimuat!")
        
        # Tampilkan sekilas info dimensi data
        c_info1, c_info2 = st.columns(2)
        c_info1.metric("Jumlah Baris DB Internal", f"{db_internal.shape[0]:,}")
        c_info2.metric("Jumlah Baris Data Target", f"{df_target.shape[0]:,}")

        # Tombol Eksekusi Mapping
        if st.button("🚀 Mulai Proses Mapping", type="primary"):
            
            # Salin data asli agar tidak mengganggu objek cache asli
            db_internal_clean = db_internal.copy()
            df_target_clean = df_target.copy()

            # 2. Standarisasi Nama Kolom
            db_internal_clean.columns = [str(c).strip().lower() for c in db_internal_clean.columns]
            df_target_clean.columns = [str(c).strip().lower() for c in df_target_clean.columns]
            
            # Daftar GAM sesuai kriteria
            target_gam_list = [
                "al irsyad islamiyah", "al jamiyatul washliyah", "alkhairat", "mui", 
                "mathla'ul anwar", "persatuan islam", "persatuan umat islam",
                "syarikat islam", "wahdah islamiyyah", "nahdlatul wathan"
            ]

            # Validasi keberadaan kolom esensial
            required_db = ['nocif', 'namacif', 'saldosmk']
            missing_db = [col for col in required_db if col not in db_internal_clean.columns]
            
            if missing_db:
                st.error(f"❌ Kolom {missing_db} tidak ditemukan di File Database Internal. Silakan periksa kembali nama kolom Anda.")
                st.stop()

            # Pembersihan tipe data
            if 'norekening' in db_internal_clean.columns:
                db_internal_clean['norekening'] = db_internal_clean['norekening'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
            
            if 'namarekening' in db_internal_clean.columns:
                db_internal_clean['namarekening'] = db_internal_clean['namarekening'].fillna("").astype(str)
            elif 'namacif' in db_internal_clean.columns:
                db_internal_clean['namarekening'] = db_internal_clean['namacif'].fillna("").astype(str)
                
            # Buat dictionary pencarian cepat (Exact Match)
            db_rek_dict = {}
            if 'norekening' in db_internal_clean.columns:
                for idx, val in db_internal_clean['norekening'].items():
                    if val and val != "nan":
                        db_rek_dict[val] = idx

            # List nama untuk Fuzzy Match
            db_names = db_internal_clean['namarekening'].tolist() if 'namarekening' in db_internal_clean.columns else []

            # Loop Pemrosesan & Progress Bar
            hasil_status, hasil_nocif, hasil_namacif, hasil_saldosmk, hasil_gam = [], [], [], [], []
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            total_rows = len(df_target_clean)

            for idx, row in df_target_clean.iterrows():
                # Update progress per 10 baris agar performa tidak terhambat UI render
                if idx % 10 == 0 or idx == total_rows - 1:
                    progress_bar.progress((idx + 1) / total_rows)
                    status_text.text(f"Memproses baris data ke-{idx + 1} dari {total_rows}...")

                rek_target = str(row['norekening']).replace(".0", "").strip() if 'norekening' in df_target_clean.columns else ""
                nama_target = str(row['namarekening']).strip() if 'namarekening' in df_target_clean.columns else ""
                gam_target = str(row['gam']).strip().lower() if 'gam' in df_target_clean.columns else ""
                
                match_idx = None
                match_type = None

                # A. Exact Match via Rekening
                if rek_target and rek_target != "nan" and rek_target in db_rek_dict:
                    match_idx = db_rek_dict[rek_target]
                    match_type = "EXACT MATCH (REKENING)"
                    
                # B. AI Fuzzy Match via Nama Rekening
                elif nama_target and nama_target.lower() != "nan" and db_names:
                    match = process.extractOne(
                        nama_target, 
                        db_names, 
                        scorer=fuzz.token_set_ratio, 
                        processor=utils.default_process
                    )
                    if match and match[1] >= threshold:
                        match_idx = match[2]
                        match_type = f"FUZZY MATCH ({match[1]:.1f}%)"

                # C. Penarikan Data Hasil Match
                if match_idx is not None:
                    hasil_status.append(f"NASABAH - {match_type}")
                    hasil_nocif.append(str(db_internal_clean.iloc[match_idx]['nocif']).replace(".0", ""))
                    hasil_namacif.append(db_internal_clean.iloc[match_idx]['namacif'])
                    hasil_saldosmk.append(db_internal_clean.iloc[match_idx]['saldosmk'])
                else:
                    hasil_status.append("BELUM NASABAH")
                    hasil_nocif.append("")
                    hasil_namacif.append("")
                    hasil_saldosmk.append("")

                # D. Deteksi GAM Target (Case-Insensitive)
                if any(g in gam_target for g in target_gam_list):
                    hasil_gam.append("TERMASUK TARGET GAM")
                else:
                    hasil_gam.append("BUKAN TARGET GAM")

            # Gabungkan kembali hasil ke dataframe asli user (mempertahankan case header awal mereka)
            df_target['Status Nasabah AI'] = hasil_status
            df_target['Hasil_NoCIF'] = hasil_nocif
            df_target['Hasil_NamaCIF'] = hasil_namacif
            df_target['Hasil_SaldoSMK'] = hasil_saldosmk
            df_target['Status GAM'] = hasil_gam

            # Bersihkan teks indikator progress
            status_text.empty()
            st.balloons()
            st.success("🎉 Proses Mapping Berhasil Diselesaikan!")

            # Preview Hasil Data
            st.subheader("📋 Cuplikan Hasil Data (10 Baris Pertama)")
            st.dataframe(df_target.head(10), use_container_width=True)

            # Menyiapkan File Download Berbasis Memory Buffer (xlsx)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_target.to_excel(writer, index=False, sheet_name='Hasil Mapping AI')
            processed_data = output.getvalue()

            # Tombol Unduh Hasil
            st.download_button(
                label="📥 Unduh File Excel Hasil Mapping",
                data=processed_data,
                file_name="HASIL_MAPPING_AI_STREAMLIT.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
else:
    st.info("💡 Silakan unggah **kedua berkas di atas** untuk memunculkan tombol proses mapping.")
