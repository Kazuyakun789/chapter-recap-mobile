import io, re, zipfile
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
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
    if re.search(r'(^|[/_.-])(logo|icon|avatar|banner|advert|ads?|thumb|thumbnail|preview)([/_.-]|$)', low):
        return False
    # URLs with image extensions are likely candidates; reader images can also have opaque URLs.
    return path.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp')) or any(
        x in low for x in ('image', 'comic', 'chapter', 'upload', 'manga', 'manhua', 'pic', 'img')
    )


def browser_extract_url_images(page_url, max_images=150, scroll_limit=100):
    """Use headless Chromium to trigger lazy-loaded chapter images while scrolling."""
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--window-size=1440,1200')
    options.add_argument('--lang=zh-CN')
    options.page_load_strategy = 'eager'

    chrome_candidates = ('/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome')
    for candidate in chrome_candidates:
        if __import__('os').path.exists(candidate):
            options.binary_location = candidate
            break

    driver = None
    errors = []
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(45)
        driver.get(page_url)
        WebDriverWait(driver, 25).until(
            lambda d: d.execute_script('return document.readyState') in ('interactive', 'complete')
        )
        # Give the reader time to initialize.
        import time
        time.sleep(3)

        previous_height = 0
        stable_rounds = 0
        for _ in range(int(scroll_limit)):
            height = driver.execute_script(
                'return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)'
            )
            viewport = driver.execute_script('return window.innerHeight')
            step = max(500, int(viewport * 0.75))
            current_y = driver.execute_script('return window.scrollY')
            next_y = min(current_y + step, max(0, height - viewport))
            driver.execute_script('window.scrollTo(0, arguments[0]);', next_y)
            time.sleep(0.7)
            new_height = driver.execute_script(
                'return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)'
            )
            if new_height == previous_height and next_y >= max(0, new_height - viewport):
                stable_rounds += 1
            else:
                stable_rounds = 0
            previous_height = new_height
            if stable_rounds >= 5:
                break
        # Some readers initialize images when scrolling back up too.
        driver.execute_script('window.scrollTo(0, 0)')
        time.sleep(1.5)

        # Collect actual rendered image URLs in DOM order, plus dimensions for filtering.
        dom_images = driver.execute_script("""
            return Array.from(document.images).map((im, index) => ({
                index: index,
                src: im.currentSrc || im.src || im.getAttribute('data-src') ||
                     im.getAttribute('data-original') || im.getAttribute('data-lazy-src') || '',
                width: im.naturalWidth || im.width || 0,
                height: im.naturalHeight || im.height || 0,
                alt: im.alt || '',
                cls: (typeof im.className === 'string' ? im.className : ''),
                parent: (im.parentElement && (im.parentElement.className || im.parentElement.id)) || ''
            }));
        """)
        resource_urls = driver.execute_script("""
            return performance.getEntriesByType('resource')
                .filter(e => e.initiatorType === 'img' || /\\.(jpg|jpeg|png|webp|gif|avif|bmp)(\\?|$)/i.test(e.name))
                .map(e => e.name);
        """)

        candidates = []
        seen = set()
        for item in dom_images:
            u = item.get('src', '')
            if not u or u.startswith(('data:', 'blob:')):
                continue
            if int(item.get('width') or 0) < 250 or int(item.get('height') or 0) < 250:
                continue
            description = f"{item.get('alt','')} {item.get('cls','')} {item.get('parent','')} {u}".lower()
            if re.search(r'logo|avatar|banner|advert|(^|[^a-z])ads?([^a-z]|$)|thumbnail|thumb|preview', description):
                continue
            if u not in seen:
                seen.add(u)
                candidates.append((u, item.get('width', 0), item.get('height', 0)))

        # Resource entries can include reader images not represented by a currently visible img node.
        for u in resource_urls:
            u = u.replace('\\u0026', '&')
            if not u.startswith(('http://', 'https://')) or u in seen or not likely_image_url(u):
                continue
            if re.search(r'logo|avatar|banner|advert|thumbnail|thumb|preview', u.lower()):
                continue
            seen.add(u)
            candidates.append((u, 0, 0))

        # Copy browser cookies to requests, which helps with sites that require a session.
        session = requests.Session()
        session.headers.update({
            'User-Agent': driver.execute_script('return navigator.userAgent'),
            'Referer': page_url,
        })
        for cookie in driver.get_cookies():
            try:
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))
            except Exception:
                pass

        results = []
        for u, dom_width, dom_height in candidates:
            if len(results) >= int(max_images):
                break
            try:
                response = session.get(u, timeout=20, stream=True)
                response.raise_for_status()
                content = response.content
                response.close()
                if not content or len(content) > 25 * 1024 * 1024:
                    continue
                item = image_from_bytes(content, f'{len(results)+1:04d}_{urlparse(u).path.split("/")[-1][:70] or "chapter_image"}')
                if not item:
                    continue
                w, h = item['image'].size
                if w < 250 or h < 250 or w * h < 100_000:
                    continue
                # If browser reported dimensions, use them to reject obvious small previews.
                if dom_width and dom_height and (dom_width < 250 or dom_height < 250):
                    continue
                results.append(item)
            except Exception as e:
                errors.append(f'{u}: {e}')
        return results, errors, len(candidates)
    finally:
        if driver is not None:
            driver.quit()


def extract_url_images(page_url, max_images=150):
    # Browser rendering is the primary path because the target reader lazy-loads images.
    return browser_extract_url_images(page_url, max_images=max_images)

st.subheader('1. Extract images from a chapter URL')
url=st.text_input('Chapter page URL', placeholder='https://example.com/chapter/123')
col1,col2=st.columns(2)
with col1: max_images=st.number_input('Maximum images',min_value=1,max_value=300,value=150,step=10)
with col2: st.write('Works best with publicly accessible pages whose image URLs are present in the page HTML.')
if st.button('🔗 Extract images from URL', type='primary'):
    if not url.startswith(('http://','https://')): st.error('Enter a complete URL beginning with http:// or https://.')
    else:
        with st.spinner('Opening the chapter in a browser and scrolling to load comic pages…'):
            try:
                got, errs, candidate_count=extract_url_images(url,int(max_images))
                if got:
                    st.session_state.image_items=got
                    st.success(f'Loaded {len(got)} images. Check their order below.')
                else: st.warning('No comic-page images were found. The site may require login, use protected/encoded images, or block automated browsers.')
                if errs: st.info(f'{len(errs)} image(s) could not be downloaded. First error: {errs[0][:300]}')
            except Exception as e: st.error(f'Could not extract images: {e}. Check that Chromium and its driver are installed in the hosting environment.')

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
