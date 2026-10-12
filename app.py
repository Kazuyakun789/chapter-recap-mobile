import io, re, zipfile
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps
import streamlit as st

st.set_page_config(page_title='Chapter Recap Mobile', page_icon='📚', layout='centered')
st.markdown('''<style>
.block-container{padding-top:1rem;padding-bottom:2rem;max-width:850px}
.stButton>button,.stDownloadButton>button{width:100%;min-height:2.8rem;border-radius:12px;font-weight:600}
[data-testid="stFileUploader"]{padding:0.5rem;border-radius:12px}
</style>''', unsafe_allow_html=True)
st.title('📚 Chapter Recap Maker — Mobile')
st.caption('Extract chapter images, arrange them, stitch them, and save the result on your phone.')

if 'image_items' not in st.session_state: st.session_state.image_items=[]
if 'notice' not in st.session_state: st.session_state.notice=''

def image_from_bytes(data, name):
    try:
        im=Image.open(io.BytesIO(data)); im.load()
        im=ImageOps.exif_transpose(im).convert('RGB')
        return {'name':name,'image':im,'data':data}
    except Exception:
        return None


def likely_image_url(u):
    low = u.lower()
    path = urlparse(u).path.lower()
    extensions = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp')
    if not path.endswith(extensions) and not any(x in low for x in ('image', 'comic', 'chapter', 'upload', 'manga', 'manhua', 'pic', 'img')):
        return False
    if re.search(r'(^|[/_.-])(logo|icon|avatar|banner|advert|ads?)([/_.-]|$)', low):
        return False
    if re.search(r'(^|[/_.-])(thumb|thumbnail|preview|cover_small|small)([/_.-]|$)', low):
        return False
    if re.search(r'/(thumb|thumbnail|avatar|icons?)/', path):
        return False
    return True


def extract_url_images(page_url, max_images=150):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    }
    resp = requests.get(page_url, headers=headers, timeout=30)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, 'html.parser')
    found, seen = [], set()

    def add_candidate(raw):
        if not raw:
            return
        raw = str(raw).strip().strip("\"'")
        raw = raw.replace("\\\\/", "/").replace("\\/", "/").replace("&amp;", "&")
        if raw.startswith(("data:", "blob:", "javascript:")):
            return
        u = urljoin(page_url, raw)
        if urlparse(u).scheme not in ("http", "https"):
            return
        if u not in seen and likely_image_url(u):
            seen.add(u)
            found.append(u)

    attrs = (
        'data-src', 'data-original', 'data-lazy-src', 'data-url', 'src',
        'data-ks-lazyload', 'data-cfsrc', 'data-img', 'data-image',
        'data-original-src', 'data-lazy', 'data-echo', 'data-actualsrc',
        'data-srcset', 'srcset', 'data-href'
    )
    for tag in soup.select('img, source, [data-src], [data-original], [data-image]'):
        for attr in attrs:
            val = tag.get(attr)
            if not val:
                continue
            if 'srcset' in attr:
                for part in str(val).split(','):
                    add_candidate(part.strip().split(' ')[0])
            else:
                add_candidate(val)

    # Some readers place image URLs in inline scripts or JSON rather than img tags.
    sources = [html] + [s.get_text(' ', strip=False) for s in soup.find_all('script')]
    pattern = re.compile(r"(?:(?:https?:)?//[^\s<>]+|(?:/[A-Za-z0-9_./%?=&+-]+))\.(?:jpg|jpeg|png|webp|gif|avif|bmp)(?:\?[^\s<>]*)?", re.I)
    for source_text in sources:
        normalized = source_text.replace("\\\\/", "/").replace("\\/", "/").replace("\\u002F", "/")
        for match in pattern.findall(normalized):
            add_candidate(match)

    results, errors = [], []
    for u in found:
        if len(results) >= int(max_images):
            break
        try:
            r = requests.get(u, headers={**headers, 'Referer': page_url}, timeout=20, stream=True)
            r.raise_for_status()
            ctype = r.headers.get('Content-Type', '').lower()
            if 'image' not in ctype and not urlparse(u).path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp')):
                r.close()
                continue
            content = r.content
            r.close()
            if not content or len(content) > 25 * 1024 * 1024:
                continue
            item = image_from_bytes(content, f'{len(results)+1:04d}_{urlparse(u).path.split("/")[-1][:70] or "image"}')
            if not item:
                continue
            width, height = item['image'].size
            if width < 250 or height < 250 or width * height < 100_000:
                continue
            results.append(item)
        except Exception as e:
            errors.append(f'{u}: {e}')
    return results, errors, len(found)

st.subheader('1. Extract images from a chapter URL')
url=st.text_input('Chapter page URL', placeholder='https://example.com/chapter/123')
col1,col2=st.columns(2)
with col1: max_images=st.number_input('Maximum images',min_value=1,max_value=300,value=150,step=10)
with col2: st.write('Works best with publicly accessible pages whose image URLs are present in the page HTML.')
if st.button('🔗 Extract images from URL', type='primary'):
    if not url.startswith(('http://','https://')): st.error('Enter a complete URL beginning with http:// or https://.')
    else:
        with st.spinner('Loading page and downloading images…'):
            try:
                got, errs, candidate_count=extract_url_images(url,int(max_images))
                if got:
                    st.session_state.image_items=got
                    st.success(f'Loaded {len(got)} images. Check their order below.')
                else: st.warning('No downloadable images were found. The site may use JavaScript rendering, login, or anti-bot protection.')
                if errs: st.info(f'{len(errs)} image(s) could not be downloaded. First error: {errs[0][:300]}')
            except Exception as e: st.error(f'Could not extract images: {e}')

st.subheader('2. Upload images from your phone')
files=st.file_uploader('Choose multiple image files',type=['png','jpg','jpeg','webp','bmp','gif'],accept_multiple_files=True)
if st.button('➕ Add uploaded images'):
    if not files: st.warning('Choose one or more image files first.')
    else:
        added=[]
        for f in files:
            item=image_from_bytes(f.getvalue(),f.name)
            if item: added.append(item)
        st.session_state.image_items.extend(added)
        st.success(f'Added {len(added)} image(s).')

st.subheader(f'3. Review and arrange images ({len(st.session_state.image_items)})')
if st.session_state.image_items:
    st.caption('Images are stitched in the order shown. Use the arrows to correct the order or remove unwanted images.')
    if st.button('🗑️ Clear all images'):
        st.session_state.image_items=[]; st.rerun()
    for i,item in enumerate(st.session_state.image_items):
        with st.container(border=True):
            a,b=st.columns([1,2])
            with a: st.image(item['image'],use_container_width=True)
            with b:
                st.markdown(f'**{i+1}.** {item["name"][:70]}')
                c1,c2,c3=st.columns(3)
                with c1:
                    if st.button('⬆️',key=f'up{i}',disabled=(i==0),help='Move up'):
                        arr=st.session_state.image_items; arr[i-1],arr[i]=arr[i],arr[i-1]; st.rerun()
                with c2:
                    if st.button('⬇️',key=f'down{i}',disabled=(i==len(st.session_state.image_items)-1),help='Move down'):
                        arr=st.session_state.image_items; arr[i+1],arr[i]=arr[i],arr[i+1]; st.rerun()
                with c3:
                    if st.button('Remove',key=f'remove{i}'):
                        st.session_state.image_items.pop(i); st.rerun()

    st.subheader('4. Stitch and download')
    max_width=st.selectbox('Output width', ['Keep widest source width','1080 px','1440 px','Original widths'],index=0)
    gap=st.number_input('Space between images (pixels)',min_value=0,max_value=100,value=0)
    if st.button('🧩 Stitch images',type='primary'):
        try:
            ims=[x['image'].copy() for x in st.session_state.image_items]
            if max_width!='Original widths':
                target=max(ims[0].width,1) if max_width=='Keep widest source width' else int(max_width.split()[0])
                scaled=[]
                for im in ims:
                    nw=target; nh=max(1,round(im.height*nw/im.width))
                    scaled.append(im.resize((nw,nh),Image.Resampling.LANCZOS))
                ims=scaled
            width=max(im.width for im in ims); height=sum(im.height for im in ims)+gap*(len(ims)-1)
            if height>100000: st.error('The stitched image would be extremely tall. Stitch fewer images at once.'); st.stop()
            out=Image.new('RGB',(width,height),'white'); y=0
            for im in ims:
                out.paste(im,((width-im.width)//2,y)); y+=im.height+gap
            buf=io.BytesIO(); out.save(buf,format='PNG',optimize=True); data=buf.getvalue()
            st.session_state['stitched']=data
            st.image(out,caption=f'Stitched image: {width} × {height}',use_container_width=True)
            st.download_button('⬇️ Download stitched PNG',data=data,file_name='stitched_chapter.png',mime='image/png')
        except Exception as e: st.error(f'Stitching failed: {e}')
    if st.button('📦 Download all source images as ZIP'):
        zbuf=io.BytesIO()
        with zipfile.ZipFile(zbuf,'w',zipfile.ZIP_DEFLATED) as z:
            for i,item in enumerate(st.session_state.image_items,1):
                ext='.png'; b=io.BytesIO(); item['image'].save(b,format='PNG')
                safe=re.sub(r'[^A-Za-z0-9._-]+','_',item['name'])[:80]
                z.writestr(f'{i:04d}_{safe}{ext}',b.getvalue())
        st.download_button('⬇️ Save images ZIP',data=zbuf.getvalue(),file_name='chapter_images.zip',mime='application/zip')
else:
    st.info('Start by extracting a URL or uploading images from your phone.')

st.divider()
st.caption('Privacy: images are processed by this hosted app while it is running. Avoid uploading private images. Public hosting does not bypass login, anti-bot protections, or website permissions.')
