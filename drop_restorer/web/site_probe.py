"""Product runtime: measure each page in real Chromium in three viewports."""
import argparse
import os
import json
from pathlib import Path
from urllib.parse import urlsplit

MEASURE = r'''(() => {
 const visible = e => {const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 const label = e => (e.getAttribute('src')||e.innerText||e.tagName).slice(0,160);
 const images=[...document.images].filter(visible);
 const root=document.documentElement;
 const main=document.querySelector('.dr-casino-content,main,#main-article,#ja-content,[role=main]')||document.body;
 const texts=[...main.querySelectorAll('p,h1,h2,h3,h4')].filter(visible);
 const button=document.querySelector('.dr-mobile-toggle');
 const menu=document.querySelector('#dr-primary-menu');
 const casino=document.querySelector('.dr-casino-toggle');
 const submenu=document.querySelector('.dr-submenu');
 return {
  width:innerWidth,height:innerHeight,document_width:root.scrollWidth,
  overflow:root.scrollWidth>innerWidth+2?root.scrollWidth-innerWidth:0,
  broken_images:images.filter(e=>!e.complete||e.naturalWidth===0).map(label),
  images_outside:images.filter(e=>{const r=e.getBoundingClientRect();return r.left < -2 || r.right>innerWidth+2;}).map(label),
  unreadable:texts.filter(e=>{const s=getComputedStyle(e);return (innerWidth<600&&parseFloat(s.fontSize)<13)||((e.scrollWidth>e.clientWidth+2||e.scrollHeight>e.clientHeight+2)&&s.overflow==='hidden');}).map(label),
  h1:[...document.querySelectorAll('h1')].map(e=>e.innerText),
  footer:!!document.querySelector('footer,[role=contentinfo],#ja-footer,#footer,#site-footer'),
  language_switcher_failed:!!document.querySelector('#lang_sel,#lang_sel_list,[id^="lang_sel_"],[class*="lang_sel"],[id*="wpml-ls"],[class*="wpml-ls"],.icl_lang_sel_widget,li.lang-item,.pll-parent-menu-item,.pll-switcher,[id*="qtranslate"],[class*="qtranslate"],#weglot_here,[id*="weglot"],[class*="weglot"],[class*="trp-language-switcher"],[id*="gtranslate"],[class*="gtranslate"],.language-switcher,.language_switcher,.language-selector,.language_selector,[id*="language-switcher"],[class*="language-switcher"],[id*="language_selector"],[class*="language_selector"],[data-language-switcher],[data-lang-switcher],img.iclflag,img.wpml-ls-flag'),
  menu_failed:innerWidth<=800 && (!button||!menu||button.getAttribute('aria-expanded')!=='true'||!visible(menu)||!casino||!submenu||casino.getAttribute('aria-expanded')!=='true'||!visible(submenu)),
  menu_position_failed:(()=>{
   const wpnav=document.querySelector('.dr-navigation[data-dr-layout="wordpress-main-slot"]');
   if(wpnav){
    const host=document.querySelector('#head-menu'),items=[...wpnav.querySelectorAll('#dr-primary-menu > li')],rects=items.map(e=>e.getBoundingClientRect()),lang=document.querySelector('#lang_sel_list'),lr=lang?.getBoundingClientRect();
    if(!host||!host.contains(wpnav))return true;
    if(innerWidth>800){const h=host.getBoundingClientRect(),split=rects.some(r=>Math.abs(r.top-rects[0].top)>2),outside=rects.some(r=>r.bottom>h.bottom+1),overlapsLanguage=lr&&rects.some(r=>r.right>lr.left-2&&r.left<lr.right+2&&r.bottom>lr.top&&r.top<lr.bottom);if(split||outside||overlapsLanguage)return true;}
   }
   const nav=document.querySelector('.dr-navigation[data-dr-layout="legacy-sidebar"]');
   if(!nav||innerWidth<=800)return false;
   const shell=nav.closest('.dr-legacy-sidebar-shell');
   const host=shell?.querySelector(':scope > .dr-legacy-sidebar-column')||nav.closest('.dr-legacy-sidebar-column');
   const content=shell?.querySelector(':scope > .dr-legacy-content-column');
   const h=host?.getBoundingClientRect();
   if(!h||h.width<100)return true;
   if(document.body.classList.contains('dr-casino-page')){const c=content?.getBoundingClientRect();return !c||c.width<320||c.left<h.right-2;}
   return false;
  })(),
  navigation_layout:(()=>{const nav=document.querySelector('.dr-navigation'),shell=nav?.closest('.dr-legacy-sidebar-shell'),host=shell?.querySelector(':scope > .dr-legacy-sidebar-column')||nav?.closest('.dr-legacy-sidebar-column'),content=shell?.querySelector(':scope > .dr-legacy-content-column'),table=document.querySelector('.dr-legacy-sidebar-layout-table');const box=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {left:Math.round(r.left),right:Math.round(r.right),width:Math.round(r.width)};};return {layout:nav?.getAttribute('data-dr-layout')||'',host:box(host),content:box(content),shell:box(shell),table:box(table),table_css_width:table?getComputedStyle(table).width:''};})(),
  menu_links:[...document.querySelectorAll('#dr-primary-menu a[href]')].map(a=>a.getAttribute('href')),
  favicon:[...document.querySelectorAll('link[rel~=icon]')].map(e=>e.href)
 };
})()'''


def main():
    os.environ['QT_QPA_PLATFORM']='offscreen'
    chromium_flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '').split()
    for flag in ('--disable-gpu', '--disable-gpu-compositing'):
        if flag not in chromium_flags:
            chromium_flags.append(flag)
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = ' '.join(chromium_flags)
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from ..core.models import Build
    from ..core.seo_contract import fingerprint, save_json
    from ..preview.server import PreviewServer
    from ..preview.window import DEVICES, LocalOnly

    parser=argparse.ArgumentParser();parser.add_argument('project',type=Path);args=parser.parse_args()
    build=Build.load(args.project);stamp=fingerprint(build)
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    server=PreviewServer(build)
    view=QWebEngineView();profile=QWebEngineProfile(view);interceptor=LocalOnly(server.port,profile)
    profile.setUrlRequestInterceptor(interceptor);profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    page=QWebEnginePage(profile,view);view.setPage(page)
    queue=[(p,device) for p in build.pages for device in DEVICES];current=[];rows=[];error=[]
    folder=build.root/'checklist-screenshots';folder.mkdir(exist_ok=True)

    def favicon_file(urls):
        from PIL import Image
        assets = (build.root / 'theme' / 'assets').resolve()
        for url in urls or []:
            path = urlsplit(url).path
            if not path.startswith('/assets/'):
                continue
            candidate = (build.root / 'theme' / path.lstrip('/')).resolve()
            if not candidate.is_relative_to(assets) or not candidate.is_file():
                continue
            try:
                with Image.open(candidate) as icon:
                    icon.load()
                    if icon.width > 0 and icon.height > 0:
                        return candidate
            except OSError:
                continue
        return None

    def advance():
        if not queue:
            app.quit();return
        p,device=queue.pop(0);current[:]=[p,device]
        print(f'LOAD {len(rows)+1}/{len(build.pages)*len(DEVICES)} {p.route} {device}',flush=True)
        view.setFixedSize(*DEVICES[device]);view.show();view.load(QUrl(server.origin+p.route))

    def collect(value):
        p,device=current
        try:
            value=json.loads(value)
        except (ValueError,TypeError):
            value=None
        if not isinstance(value,dict):
            error.append(p.route+': не удалось прочитать DOM');app.quit();return
        if value.get('menu_position_failed'):
            error.append(p.route+': Casino или другой пункт меню вышел из основной строки под баннером.')
            app.quit();return
        if value.get('language_switcher_failed'):
            error.append(p.route+': в восстановленной странице остался переключатель языков или флажок.')
            app.quit();return
        value.update(route=p.route,key=p.key,device=device)
        icon_path = favicon_file(value.get('favicon'))
        value['favicon_failed'] = icon_path is None
        if icon_path is not None:
            from PIL import Image
            icon_image = folder / ('favicon_' + p.key + '.png')
            try:
                with Image.open(icon_path) as icon:
                    icon.convert('RGBA').resize((32, 32)).save(icon_image)
                value['favicon_screenshot'] = icon_image.relative_to(build.root).as_posix()
            except OSError:
                value['favicon_failed'] = True
        image=folder/(device.lower()+'_'+p.key+'.png')
        if not view.grab().save(str(image)):
            error.append('Не удалось сохранить снимок');app.quit();return
        value['screenshot']=image.relative_to(build.root).as_posix();rows.append(value)
        print(p.route+' '+device,flush=True)
        if p.casino and build.seo_policy.get('widget_id'):
            page.runJavaScript("document.querySelector('.dr-casino-table')?.scrollIntoView({block:'start'});")
            QTimer.singleShot(250,table_capture)
        else:
            advance()

    def table_capture():
        p,device=current
        image=folder/(device.lower()+'_table_'+p.key+'.png')
        if view.grab().save(str(image)):
            rows[-1]['table_screenshot']=image.relative_to(build.root).as_posix()
        advance()

    def measure():
        page.runJavaScript('JSON.stringify('+MEASURE+')',collect)

    def ready():
        if current[1] in ('Mobile','Tablet'):
            # Dispatch real click events in the page. Coordinate clicks are not
            # reliable in an offscreen WebEngine surface when a legacy document
            # is wider than the viewport, and produced false menu failures.
            page.runJavaScript("(()=>{const mobile=document.querySelector('.dr-mobile-toggle');if(mobile)mobile.click();const casino=document.querySelector('.dr-casino-toggle');if(casino)casino.click();return true;})()")
            QTimer.singleShot(180,measure)
        else:
            measure()

    def loaded(ok):
        if not ok:
            error.append('Страница не загрузилась: '+current[0].route+' ('+current[1]+').');app.quit()
        else:
            # Force lazy images to load before checking the entire document,
            # including cards below the fold. Missing files still fail normally.
            page.runJavaScript("document.querySelectorAll('img[loading=lazy]').forEach(e=>e.loading='eager');")
            QTimer.singleShot(650,ready)

    def timed_out():
        error.append(f'Истекло время проверки: завершено {len(rows)}/{len(build.pages)*len(DEVICES)}; '
                     + (current[0].route+' ('+current[1]+').' if current else 'браузер не начал загрузку.'))
        app.quit()

    view.loadFinished.connect(loaded);QTimer.singleShot(0,advance);QTimer.singleShot(max(180000,len(queue)*10000),timed_out)
    app.exec();view.stop();view.setPage(QWebEnginePage(view));page.deleteLater();app.processEvents();view.close();server.stop()
    if fingerprint(Build.load(build.root))!=stamp:
        error.append('Сборка изменилась во время проверки. Повторите проверку для текущей версии.')
    if not error and len(rows)!=len(build.pages)*len(DEVICES):
        error.append(f'Браузер завершился досрочно: проверено {len(rows)}/{len(build.pages)*len(DEVICES)}.')
    valid=not error and len(rows)==len(build.pages)*len(DEVICES)
    save_json(build.root/'visual-checks.json',{'fingerprint':stamp if valid else '', 'pages':rows,'errors':error,'complete':valid})
    for message in error:
        print('ERROR: '+message,flush=True)
    return 0 if valid else 1


if __name__=='__main__':
    raise SystemExit(main())
