'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let token = document.querySelector('meta[name="csrf-token"]')?.content || '';
const devices = {Desktop:[1920,1080],Tablet:[768,1024],Mobile:[375,812]};
const titles = {ready:'Тема готова к просмотру',site_review:'Ожидает вашего решения по сайту',metadata_review:'Ожидает выбора метаданных',downloading:'Загрузка не завершена',incomplete:'Незавершённая сборка'};
let state, lastRevision = -1, selected = '', preview, loaded = false, editing, loading = false, autoCaptured = new Set();
let openedId = null, agentSettingsLoaded = false, defaultSettingsLoaded = false;
let projectTimer, projectsLoading=false, projectSignature='', deleteDraft=null;

async function refreshToken() {
  let response;
  try {
    response = await fetch('/?token_refresh=' + Date.now(), {cache:'no-store', headers:{'Cache-Control':'no-cache'}});
  } catch (error) {
    throw new Error('Связь с локальным инструментом потеряна. Проверьте http://127.0.0.1:8780/health.');
  }
  if (!response.ok) throw new Error('Локальная панель недоступна. Проверьте http://127.0.0.1:8780/health.');
  const html = await response.text();
  const match = html.match(/<meta\s+name=["']csrf-token["']\s+content=["']([^"']+)["']/i);
  if (!match) throw new Error('Не удалось обновить сеанс панели. Обновите страницу вручную.');
  token = match[1];
}

async function api(path, values, retry=true) {
  let response;
  try {
    response = await fetch('/api/' + path, {method:values === undefined ? 'GET':'POST', headers:{'X-DropRestorer-Token':token,...(values === undefined ? {}:{'Content-Type':'application/json'})}, ...(values === undefined ? {}:{body:JSON.stringify(values)})});
  } catch (error) {
    if (retry) {
      await new Promise(resolve => setTimeout(resolve, 250));
      try { await refreshToken(); return api(path, values, false); } catch (ignored) {}
    }
    throw new Error('Связь с локальным инструментом потеряна. Проверьте http://127.0.0.1:8780/health.');
  }
  if (response.status === 403 && retry) {
    try { await refreshToken(); return api(path, values, false); }
    catch (error) { throw new Error(error.message || 'Сеанс панели устарел. Обновите страницу.'); }
  }
  let result;
  try { result = await response.json(); }
  catch (error) { throw new Error('Локальный инструмент вернул некорректный ответ. Проверьте его состояние на /health.'); }
  if (!response.ok) throw new Error(result.error || 'Не удалось выполнить действие.');
  return result;
}
function notice(message, error=false) { $('notice').hidden=false; $('notice').classList.toggle('error',error); $('notice').textContent=message; }
function renderLogs(lines) {
  const output=$('log-lines'), follow=output.scrollHeight-output.scrollTop-output.clientHeight<45;
  output.textContent=lines.join('\n') || 'Пока нет сообщений.';
  if(follow) output.scrollTop=output.scrollHeight;
}
async function perform(fn) { try { $('notice').hidden=true; await fn(); await refresh(true); } catch(error) { notice(error.message,true); } }
function tab(name) {
  if (!$(name)?.classList.contains('panel')) name='projects';
  document.querySelectorAll('.panel').forEach(el => el.hidden=el.id!==name);
  document.querySelectorAll('.sidebar [data-tab]').forEach(el => el.classList.toggle('active',el.dataset.tab===name));
  if (location.hash !== '#'+name) history.replaceState(null,'','#'+name);
  if(name==='projects') projects().catch(error=>notice(error.message,true));
  if(name==='preview'){
    resize();
    requestAnimationFrame(()=>{
      $(name)?.scrollIntoView({block:'start'});
      resetPreviewScroll();
    });
  }
}
document.querySelectorAll('[data-tab]').forEach(el=>el.addEventListener('click',()=>tab(el.dataset.tab)));
window.addEventListener('hashchange',()=>tab(location.hash.slice(1)));
$('job-toggle').onclick=()=>{
  const collapsed=$('job').classList.toggle('collapsed');
  $('job-toggle').textContent=collapsed?'Развернуть':'Свернуть';
  $('job-toggle').setAttribute('aria-expanded',String(!collapsed));
};
function id() { if(!state?.build) throw new Error('Сначала выберите сборку.'); return state.build.id; }
function money(usage) { return usage?.cost_usd == null ? 'Неизвестно' : '$'+Number(usage.cost_usd).toFixed(usage.cost_usd ? 5:2); }
function packageLinks(archive) {
  if(!archive) return '';
  if(archive.kind!=='wordpress_theme') return '<p>Для обычной установки WordPress получите установочный ZIP ниже.</p>';
  return `<div class="card"><h2>Файлы для установки WordPress</h2><div class="downloads"><a href="${esc(archive.url)}" download>1. Скачать тему для WordPress · ${(archive.bytes/1024/1024).toFixed(1)} МБ</a><a href="${esc(archive.content.url)}" download>2. Скачать страницы для импорта (XML)</a></div><p>Загрузите ZIP через «Внешний вид → Темы → Добавить тему». Затем импортируйте XML через «Инструменты → Импорт → WordPress» и выберите главную страницу в настройках чтения.</p><p class="ok">Структура ZIP проверена: style.css и обязательные файлы находятся в корне папки темы.</p><details><summary>Полный комплект и отчёты</summary><a href="${esc(archive.bundle.url)}" download>Скачать полный комплект</a><p class="muted">Комплект содержит тему, страницы, снимки и отчёты. Для установщика тем используйте первый ZIP.</p></details></div>`;
}

async function projects() {
  clearTimeout(projectTimer);
  if(projectsLoading || $('delete-dialog').open) return;
  projectsLoading=true;
  try {
    const rows=await api('projects'), signature=JSON.stringify([rows,state?.build?.id]);
    if(signature!==projectSignature) {
      projectSignature=signature;
      const labels={ready:'Тема готова',site_review:'Проверка сайта',metadata_review:'Метаданные',downloading:'Скачивание',incomplete:'Не завершена'};
      $('projects-list').innerHTML=rows.length ? rows.map(row=>{
        const shot=row.thumbnail, current=row.id===state?.build?.id, disabled=row.can_open?'':' disabled data-blocked="true"';
        const stamp=row.id.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
        const date=stamp?new Date(+stamp[1],+stamp[2]-1,+stamp[3],+stamp[4],+stamp[5]).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'';
        return `<article class="card project-card ${current?'selected':''}" data-project="${esc(row.id)}"><button class="project-cover" data-open="${esc(row.id)}" data-idle aria-label="Открыть сборку «${esc(row.title)}»"${disabled}><span class="cover-placeholder"><svg viewBox="0 0 48 40" aria-hidden="true"><rect x="2" y="2" width="44" height="36" rx="4"/><path d="M2 12h44M9 7h1m5 0h1m5 0h1M10 21h16m-16 7h28"/></svg><span>${esc(shot?.message || 'Главная страница')}</span></span>${shot?.url?`<img src="${esc(shot.url)}" alt="Скриншот главной страницы: ${esc(row.title)}" loading="lazy" decoding="async">`:''}<span class="badge project-status ${esc(row.status)}">${esc(labels[row.status] || row.status)}</span></button><div class="project-body"><h2 title="${esc(row.title)}">${esc(row.title)}</h2>${row.url?`<a class="project-url" href="${esc(row.url)}" target="_blank" rel="noopener noreferrer">${esc(row.url)} <span aria-hidden="true">↗</span></a>`:'<p class="project-url muted">Адрес не сохранён</p>'}<div class="project-meta"><span>${row.pages?countLabel(row.pages,['страница','страницы','страниц']):'Страницы не загружены'}</span><time>${esc(date)}</time>${current?'<span class="project-current">Открыта</span>':''}</div><div class="project-actions"><button class="project-open" data-open="${esc(row.id)}" data-idle${disabled}>Открыть сборку <span aria-hidden="true">→</span></button><button class="project-delete" data-delete="${esc(row.id)}" data-idle aria-label="Удалить сборку «${esc(row.title)}»"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M7 5V3h6v2M5 5l1 12h8l1-12M8 8v6m4-6v6"/></svg>Удалить</button></div><p class="project-id">${esc(row.id)}</p></div></article>`;
      }).join('') : '<div class="empty library-empty"><h2>Здесь будут ваши сайты</h2><p>Восстановите первый архив — сохраним сборку и снимок её главной страницы.</p></div>';
      $('projects-list').querySelectorAll('[data-open]').forEach(button=>button.onclick=()=>perform(async()=>{openedId=button.dataset.open;await api('projects/open',{id:openedId});}));
      $('projects-list').querySelectorAll('[data-delete]').forEach(button=>button.onclick=()=>perform(()=>askDelete(button.dataset.delete)));
      $('projects-list').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.hidden=true;img.parentElement.querySelector('.cover-placeholder > span').textContent='Снимок пока недоступен';}));
    }
    controls();
    if(rows.some(row=>row.thumbnail?.status==='pending') && location.hash==='#projects') projectTimer=setTimeout(()=>projects().catch(error=>notice(error.message,true)),1800);
  } finally { projectsLoading=false; }
}
function byteSize(value) { const units=['Б','КБ','МБ','ГБ']; let index=0; while(value>=1024 && index<3){value/=1024;index++;} return value.toLocaleString('ru-RU',{maximumFractionDigits:index?1:0})+' '+units[index]; }
function countLabel(count, words) { const last=count%10, teen=count%100; return count+' '+words[teen>=11 && teen<=14?2:last===1?0:last>=2 && last<=4?1:2]; }
async function askDelete(identifier) {
  deleteDraft=await api('projects/delete-info',{id:identifier});
  $('delete-title').textContent=deleteDraft.title;
  $('delete-url').textContent=deleteDraft.url;
  $('delete-size').textContent=countLabel(deleteDraft.files,['файл','файла','файлов'])+' · '+byteSize(deleteDraft.bytes);
  $('delete-id').textContent=deleteDraft.id;
  $('delete-error').hidden=true;$('delete-confirm').disabled=false;
  $('delete-dialog').showModal();$('delete-cancel').focus();
}
$('delete-cancel').onclick=()=>$('delete-dialog').close();
$('delete-dialog').addEventListener('close',()=>{deleteDraft=null;projects().catch(error=>notice(error.message,true));});
$('delete-confirm').onclick=async()=>{
  if(!deleteDraft) return;
  $('delete-confirm').disabled=true;
  try {
    await api('projects/delete',{id:deleteDraft.id,confirmation:deleteDraft.confirmation,confirmed:true});
    $('delete-dialog').close();await refresh(true);
  } catch(error) {
    $('delete-error').textContent=error.message;$('delete-error').hidden=false;$('delete-confirm').disabled=false;
  }
};
function controls() {
  if(!state) return;
  const busy=state.job.busy, build=state.build, ready=build?.status==='ready', site=build?.status==='site_review', pending=build?.status==='metadata_review';
  $('cancel').hidden=state.job.cancellable===false;
  document.querySelectorAll('[data-idle]').forEach(el=>el.disabled=busy);
  $('propose').disabled=busy || !pending || !state.active_agent;
  const layoutEditable=!!build && ['metadata_review','site_review','ready'].includes(build.status);
  $('casino-layout-form').querySelectorAll('input,button').forEach(el=>{el.disabled=busy || !layoutEditable;});
  $('sample-metadata').disabled=busy || !(pending || site || ready) || !state.active_agent || !$('sample-page').value;
  $('sample-page').disabled=busy || !(pending || site || ready);
  $('keep').disabled=busy || !pending;
  $('apply-meta').disabled=busy || !pending || !build?.can_apply;
  $('reopen').hidden=pending; $('reopen').disabled=busy || !build;
  $('run-checks').disabled=busy || !ready;
  $('capture-one').disabled=$('capture-all').disabled=busy || !(ready || site) || !loaded;
  $('package').disabled=busy || !(ready || site || pending) || !loaded || !$('confirmed').checked || !preview || preview.stage!==build?.status;
  $('package').textContent=pending?'Одобрить без изменений — продолжить':site?'Одобрить сайт — создать тему':'Одобрить и упаковать';
  $('logo').disabled=busy || !(ready || site) || !state.active_agent;
  $('favicon').disabled=$('favicon-topic').disabled=busy || !(ready || site);
  $('favicon-info').textContent=build?.favicon?.label ? 'Фавикон: '+build.favicon.label+'. '+(build.favicon.needs_topic_review?'Уточните тему в списке.':'Создан и подключён к страницам.') : 'При отсутствии архивного фавикона иконка создаётся автоматически по тематике сайта. Агент необязателен.';
  $('favicon-preview').hidden=!build?.favicon?.preview_data;
  if(build?.favicon?.preview_data && $('favicon-preview').src!==build.favicon.preview_data) $('favicon-preview').src=build.favicon.preview_data;
  $('edit-toggle').disabled=busy || !(ready || site);
  $('open-preview').disabled=$('metadata-preview').disabled=busy || !(ready || pending || site);
  $('approval').hidden=!(ready || site || pending);
  const counts=build?.checklist?.counts;
  $('approval-hint').textContent=pending?'Продолжим с исходными метаданными. Агент не требуется.':
    (counts?`Чек-лист: ошибок ${counts.fail || 0}, требуют просмотра ${counts.review || 0}, ещё не проверено ${counts.not_checked || 0}. `:'')+
    (build?.report?`Проверки темы: ${build.report.errors} ошибок, ${build.report.warnings} замечаний. `:'')+
    'Вы можете принять текущий результат. Замечания и ваше одобрение сохранятся в отчёте. Агент не требуется.';
  document.querySelectorAll('[data-blocked="true"]').forEach(el=>el.disabled=true);
}
async function refresh(force=false) {
  if(loading) return;
  loading=true;
  try {
    const next=await api('state');
    const changed=force || lastRevision!==next.job.revision || selected!==next.build?.id;
    state=next;
    $('current').textContent=state.build ? state.build.origin+' · '+state.build.pages.length+' страниц' : 'Сборка не выбрана';
    $('agent-badge').textContent=state.agents.find(a=>a.id===state.active_agent)?.model || 'Без агента';
    $('job').hidden=!state.job.busy; $('job-text').textContent=state.job.message; $('progress').value=state.job.progress; renderJobActivity();
    if(changed) {
      if(state.job.error) notice(state.job.error,true);
      if(selected!==state.build?.id || (preview && preview.receipt!==state.preview_receipt)) clearPreview();
      selected=state.build?.id; lastRevision=state.job.revision;
      renderMetadata(); renderChecks(); renderAgents(); renderSettings(); if(typeof renderSeo==='function') renderSeo();
      $('archive-result').innerHTML=packageLinks(state.build?.archive);
      renderLogs(state.logs);
      if(location.hash==='#projects') await projects();
      if(openedId && !state.job.busy) {
        openedId=null;
        if(!state.job.error) tab(state.build?.status==='metadata_review'?'metadata':state.build?.status==='site_review'?'seo':'checks');
      }
    }
    if(state.job.busy) renderLogs(state.logs);
    controls();
  } catch(error) { notice('Связь с локальным инструментом потеряна. '+error.message,true); }
  finally { loading=false; }
}
function renderMetadata() {
  const build=state.build;
  $('metadata-empty').hidden=!!build; $('metadata-content').hidden=!build;
  if(!build) return;
  const layout=build.seo_policy?.casino_layout || {content_width_px:1120,table_width_px:0,row_height_px:110,cell_padding_px:12,cell_widths_px:{}};
  const fields={content_width_px:layout.content_width_px ?? 1120,table_width_px:layout.table_width_px ?? 0,row_height_px:layout.row_height_px ?? 110,cell_padding_px:layout.cell_padding_px ?? 12};
  Object.entries(fields).forEach(([key,value])=>{const el=$('casino-layout-form').elements[key];if(el) el.value=value;});
  ['logo','bonus','characteristics','rating','button'].forEach(key=>{const el=$('casino-layout-form').elements['cell_'+key];if(el) el.value=layout.cell_widths_px?.[key] ?? 0;});
  const pending=build.status==='metadata_review';
  $('metadata-status').textContent=pending?'Страницы скачаны. Можно одобрить исходные метаданные и продолжить без агента.':(build.status==='site_review'?'Страницы подготовлены. Одобрите сайт в SEO-чек-листе или превью, чтобы создать тему. ':'Тема создана. ')+ 'Выбор метаданных: '+(build.metadata_decision==='keep'?'оставить исходные':build.metadata_decision==='agent'?'варианты агента':'сохранённые данные')+'.';
  $('casino-layout-help').textContent=build.status==='ready'
    ?'Эти значения меняют оформление казино-страниц и таблиц в готовой теме WordPress. Сохранение обновит тему и отменит прежнее одобрение упаковки: после этого снова откройте превью и подтвердите новую версию.'
    :'Эти параметры применяются только к трём созданным страницам казино и всему их содержимому. Значение 0 у ширины означает всю доступную ширину контейнера; в старой двухколоночной теме боковое меню сохраняется, а контент автоматически сжимается до ширины экрана.';
  $('metadata-list').innerHTML=build.pages.map(page=>{
    const row=build.metadata_review[page.key];
    if(page.casino) return `<article class="card metadata-page"><h2>${esc(page.title)} <span class="badge">Казино</span></h2><div class="route">${esc(page.route)}</div><p class="muted">Контент и метаданные задаёт владелец в WordPress. Каноникл: ${esc(build.origin+page.route)}</p></article>`;
    const block=(label,value,notes)=>`<div class="metadata-value"><h3>${label}</h3><dl><dt>Title · ${value?.title?.length || 0} знаков</dt><dd>${esc(value?.title || 'Отсутствует')}</dd><dt>Description · ${value?.description?.length || 0} знаков</dt><dd>${esc(value?.description || 'Отсутствует')}</dd></dl>${notes?.length?'<ul class="notes">'+notes.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':''}</div>`;
    return `<article class="card metadata-page"><h2>${esc(page.title)}</h2><div class="route">${esc(page.route)}<br>Каноникл: ${esc(build.origin+page.route)}</div><div class="cols">${block('Исходные метаданные',row?.original,build.issues[page.key])}${row?.proposed?block('Предложение агента',row.proposed,build.proposed_issues[page.key]):'<div class="metadata-value"><h3>Предложение агента</h3><p class="muted">'+esc(row?.error || 'Агент ещё не готовил варианты для этой страницы.')+'</p></div>'}</div></article>`;
  }).join('');
}
function renderChecks() {
  const build=state.build, r=build?.report;
  if(!r) {
    $('checks-content').className=build?'':'empty';
    if(build?.status==='site_review') {
      const c=build.checklist?.counts || {};
      $('checks-content').innerHTML=`<div class="card"><h2>Сайт подготовлен — решение за вами</h2><p>Чек-лист: ошибок ${c.fail || 0}, требуют просмотра ${c.review || 0}, ещё не проверено ${c.not_checked || 0}. Замечания сохранятся при одобрении. Агент не требуется.</p><div class="actions"><button id="checks-next" class="primary" data-idle>Одобрить сайт — создать тему</button><button id="checks-details" data-idle>Открыть SEO-чек-лист</button></div></div>`;
      $('checks-next').onclick=()=>perform(advanceOwner);$('checks-details').onclick=()=>tab('seo');
    } else {
      $('checks-content').textContent=build?.status==='metadata_review'?'Сначала одобрите страницы на вкладке «Страницы и метаданные» или в превью. Подключение агента необязательно.':'Отчёт появится после подготовки сайта.';
    }
    return;
  }
  $('checks-content').className='';
  const labels={links:'Ссылки',languages:'Языки',scripts:'Скрипты',junk:'Мусор',canonical:'Канониклы',navigation:'Меню',metadata:'Мета',export:'WordPress'};
  const findings=r.findings || [];
  $('checks-content').innerHTML=`<div class="card"><h2>Тема готова к вашему решению</h2><p>Посмотрите сайт и одобрите упаковку. Замечания сохранятся в отчёте; подключать агента не требуется.</p><button id="checks-preview" class="primary" data-idle>Посмотреть и одобрить упаковку</button></div><div class="stat-grid"><div class="stat"><b>${r.pages.length}</b><span>проверено страниц</span></div><div class="stat"><b class="${r.errors?'bad':'ok'}">${r.errors}</b><span>ошибок · ${r.warnings} замечаний</span></div><div class="stat"><b>${esc(money(r.usage))}</b><span>API этой сборки</span></div><div class="stat"><b>${r.usage?.calls ?? '—'}</b><span>запросов агента</span></div></div><div class="card"><h2>${r.passed?'Проверки пройдены':'Есть замечания — решение за вами'}</h2><p class="muted">Проверено ${esc(new Date(r.checked_at).toLocaleString('ru-RU'))}</p><div class="table-scroll"><table><thead><tr><th>Страница</th>${Object.values(labels).map(x=>'<th>'+x+'</th>').join('')}</tr></thead><tbody>${r.pages.map(p=>'<tr><td>'+esc(p.title || p.route)+'<br><small>'+esc(p.route)+'</small></td>'+Object.keys(labels).map(k=>'<td class="'+(p.checks[k]?'bad':'ok')+'">'+(p.checks[k] || '✓')+'</td>').join('')+'</tr>').join('')}</tbody></table></div></div><div class="card"><h2>Подробности по страницам</h2>${r.pages.map(p=>{const list=findings.filter(f=>f.route===p.route);return '<details><summary>'+esc(p.title || p.route)+' · '+list.length+' замечаний</summary><p class="muted">Каноникл: '+esc(p.canonical)+'</p>'+(p.casino?'<p>Метаданные казино задаёт владелец.</p>':'')+(list.length?'<ul>'+list.map(f=>'<li class="'+(f.severity==='error'?'bad':'warning')+'">'+esc(f.detail)+'</li>').join('')+'</ul>':'<p class="ok">Ошибок не найдено.</p>')+'</details>';}).join('')}${findings.some(f=>!r.pages.some(p=>p.route===f.route))?'<h3>Общие замечания</h3><ul>'+findings.filter(f=>!r.pages.some(p=>p.route===f.route)).map(f=>'<li>'+esc(f.detail)+'</li>').join('')+'</ul>':''}</div><div class="card"><h2>Удалённые элементы</h2><p>${Object.entries(r.removal_counts || {}).map(([k,v])=>esc(labels[k] || k)+': '+v).join(' · ') || 'Нет записей об удалении'}</p><details><summary>Журнал очистки</summary><ul>${(r.removed || []).map(x=>'<li>'+esc([x.route,x.kind,x.detail,x.asset,x.count?'количество: '+x.count:''].filter(Boolean).join(' · '))+'</li>').join('')}</ul></details></div><div class="card"><h2>Расходы агента</h2><p>${esc(money(r.usage))} · вызовов: ${r.usage?.calls ?? 'нет данных'} · входных токенов: ${r.usage?.prompt_tokens ?? 'нет данных'} · выходных: ${r.usage?.completion_tokens ?? 'нет данных'}</p>${r.usage?.cost_usd==null?'<p class="muted">Не все данные о стоимости или прошлых вызовах известны. Подтверждённая часть: $'+Number(r.usage?.known_cost_usd || 0).toFixed(5)+'.</p>':''}<details><summary>Запросы и стоимость</summary><ul>${(r.usage?.events || []).map(e=>'<li>'+esc([e.at,e.model,e.operation,e.status,e.cost_usd==null?'стоимость неизвестна':'$'+e.cost_usd].join(' · '))+'</li>').join('') || '<li>Запросов не было.</li>'}</ul></details></div><p class="scope">${esc(typeof r.scope==='string'?r.scope:JSON.stringify(r.scope))}</p>`;
  $('checks-preview').onclick=()=>perform(openPreview);
  if(build.can_repackage) {
    const card=$('checks-preview').closest('.card');
    card.querySelector('h2').textContent=build.archive?'Сайт одобрен и упакован':'Сайт уже одобрен';
    card.querySelector('p').textContent=build.archive?'Файлы для установки доступны выше. Сохранённые замечания можно изучить в отчёте ниже.':'Получите установочный ZIP кнопкой выше. Повторное восстановление сайта не требуется.';
    $('checks-preview').textContent='Посмотреть сайт';
  }
  $('checks-content').insertAdjacentHTML('afterbegin',packageLinks(build.archive)+(build.can_repackage && !build.archive?'<div class="card"><h2>Установочный ZIP для одобренного сайта</h2><p>Получите тему для обычного установщика WordPress. Содержимое одобренного сайта сохранится.</p><button id="repackage" class="primary" data-idle>Получить установочный ZIP</button></div>':''));
  if($('repackage')) $('repackage').onclick=()=>perform(()=>api('repackage',{id:id()}));
}
function renderAgents() {
  $('agents-list').innerHTML=state.agents.map(agent=>`<article class="card agent-row"><div><h2>${esc(agent.model)} ${agent.id===state.active_agent?'<span class="badge">Активный</span>':''}</h2><p class="muted">${esc(agent.endpoint)}</p></div><div class="actions"><button data-agent="${agent.id}" data-op="select" data-idle>Выбрать</button><button data-agent="${agent.id}" data-op="test" data-idle>Тест подключения</button><button data-agent="${agent.id}" data-op="disconnect" data-idle>Отключить</button></div></article>`).join('');
  $('agents-list').querySelectorAll('[data-op]').forEach(el=>el.onclick=()=>perform(()=>api('agents',{action:el.dataset.op,agent:el.dataset.agent})));
  $('connection-cost').textContent='Тесты подключения учитываются отдельно от сайта. Стоимость: '+money(state.connection_usage)+'.';
  if(!agentSettingsLoaded){['endpoint','model'].forEach(k=>{if(state.settings[k]) $('agent-form').elements[k].value=state.settings[k];});agentSettingsLoaded=true;}
  const build=state.build, picker=$('sample-page'), previous=picker.dataset.project===build?.id?picker.value:'';
  picker.innerHTML=(build?.pages || []).filter(p=>!p.casino).map(p=>`<option value="${esc(p.key)}">${esc(p.title)} · ${esc(p.route)}</option>`).join('');
  picker.dataset.project=build?.id || '';
  if([...picker.options].some(o=>o.value===previous)) picker.value=previous;
  $('sample-hint').textContent=!build?'Сначала откройте сборку со скачанными страницами.':!state.active_agent?'Подключите модель выше. Для OpenRouter API-ключ нужен и при выборе бесплатной модели.':'Один запрос для одной страницы. Расходы попадут в журнал этой сборки. Метаданные казино задаёт владелец.';
  if(build) $('sample-hint').textContent+=' Расходы агента в этой сборке: '+money(build.usage)+' · запросов: '+(build.usage?.calls ?? 'нет данных')+'.';
  const sample=build?.metadata_sample;
  if(!sample){$('sample-result').textContent='';return;}
  const block=(label,value)=>`<div class="metadata-value"><h3>${label}</h3><dl><dt>Title · ${value?.title?.length || 0} знаков</dt><dd>${esc(value?.title || 'Отсутствует')}</dd><dt>Description · ${value?.description?.length || 0} знаков</dt><dd>${esc(value?.description || 'Отсутствует')}</dd></dl></div>`;
  $('sample-result').innerHTML=`<h3>${esc(sample.page_title)}</h3><p class="route">${esc(sample.route)} · ${esc(sample.model)}</p>${sample.stale?'<p class="warning">Страница изменилась после этой пробы. Сгенерируйте новый пример для актуального содержания.</p>':''}${sample.status==='failed'?'<p class="bad">'+esc(sample.error)+'</p>':sample.status==='pending'?'<p class="muted">Пример ещё не получен. Если запрос был прерван, повторите пробу.</p>':'<div class="cols">'+block('Текущие метаданные на момент пробы',sample.original)+block('Предложение модели',sample.proposed)+'</div>'+((sample.issues || []).length?'<ul class="notes">'+sample.issues.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'<p class="ok">Длина соответствует ориентирам; совпадений с метаданными других восстановленных страниц нет.</p>')+'<p class="muted">Проверьте смысл и язык по содержимому страницы. Предложение не применено к сайту.</p>'}`;
}
function bytes(value) {
  value=Number(value)||0;
  if(value<1024) return value+' B';
  if(value<1048576) return (value/1024).toFixed(1)+' KB';
  return (value/1048576).toFixed(1)+' MB';
}
function renderJobActivity() {
  const a=state?.job?.activity, box=$('job-details');
  if(!state?.job?.busy || !a || (!a.page && !a.url)) { box.hidden=true; return; }
  box.hidden=false;
  $('job-page').textContent=a.page ? `${a.page} / ${a.total || '—'}` : '—';
  const done=Number(a.downloaded)||0, cached=Number(a.cached)||0, skipped=Number(a.skipped)||0;
  $('job-files').textContent=skipped ? `${done+cached} сохранено · ${skipped} пропущено` : `${done+cached} сохранено`;
  $('job-size').textContent=bytes((Number(a.bytes)||0)+(Number(a.request_bytes)||0));
  const speed=(a.status==='receiving'||a.status==='received') && Number(a.request_seconds) ?
    Number(a.request_bytes)/Number(a.request_seconds) : Number(a.speed_bps)||0;
  $('job-speed').textContent=speed ? bytes(speed)+'/с' : '—';
  const age=a.updated_at ? Math.max(0,Math.round((Date.now()-Date.parse(a.updated_at))/1000)) : 0;
  $('job-age').textContent=age<2?'сейчас':`${age} с назад`;
  const labels={requesting:'Запрос',receiving:'Получение',received:'Получен',retrying:'Повтор',failed:'Ошибка',
    downloading:'Загрузка',downloaded:'Сохранён',cached:'Из кэша',skipped:'Пропущен',page:'Страница'};
  let status=labels[a.status] || 'Работа';
  if(a.status==='retrying' && a.attempt) status+=` ${a.attempt}/3`;
  if(a.http_status) status+=` · HTTP ${a.http_status}`;
  $('job-resource-status').textContent=status;
  $('job-resource-url').textContent=a.url || a.resource || a.route || 'Подготовка страницы…';
}
function renderSettings(){ if(defaultSettingsLoaded) return; ['lang','final_domain','auto_screens'].forEach(k=>{const el=$('settings-form').elements[k]; if(el.type==='checkbox') el.checked=!!state.settings[k]; else el.value=state.settings[k] || ''; if($('restore-form').elements[k]) $('restore-form').elements[k].value=state.settings[k] || '';}); defaultSettingsLoaded=true; }
function clearPreview(){ preview=null; loaded=false; editing=null; $('site-frame').src='about:blank'; $('preview-empty').hidden=false; $('preview-content').hidden=true; $('confirmed').checked=false; $('archive-result').textContent=''; $('editor').hidden=true; $('html-label').hidden=true; $('save-html').hidden=true; $('files-list').hidden=true; }
function resetPreviewScroll(){
  const viewport=$('viewport');
  if(viewport) viewport.scrollTop=0;
  const frame=$('site-frame');
  try{
    const win=frame?.contentWindow, doc=win?.document;
    win?.scrollTo(0,0);
    if(doc?.documentElement) doc.documentElement.scrollTop=0;
    if(doc?.body) doc.body.scrollTop=0;
  }catch(_error){ /* cross-origin or not loaded yet; the load handler retries */ }
}
function resize(){ if(!preview) return; const [w,h]=devices[$('device').value], viewport=$('viewport'); const available=Math.max(200,viewport.clientWidth-(innerWidth<=720?18:34)); const z=$('zoom').value==='fit'?Math.min(1,available/w):Number($('zoom').value); const frame=$('site-frame'); frame.style.width=w+'px';frame.style.height=h+'px';frame.style.transform=`scale(${z})`; $('frame-size').style.width=w*z+'px';$('frame-size').style.height=h*z+'px'; }
function navigate(){ if(!preview) return; loaded=false;$('confirmed').checked=false;resetPreviewScroll();$('site-frame').src=preview.origin+$('page-picker').value;controls(); }
async function openPreview(){ preview=await api('preview',{id:id()}); $('page-picker').innerHTML=state.build.pages.map(p=>`<option value="${esc(p.route)}">${esc(p.title)}${p.casino?' · Казино':''}</option>`).join('');$('preview-empty').hidden=true;$('preview-content').hidden=false;tab('preview');resize();resetPreviewScroll();navigate(); if(state.settings.auto_screens && state.build.status==='ready' && !autoCaptured.has(id())){autoCaptured.add(id());await api('screenshots',{id:id()});} }
window.addEventListener('message',event=>{
  if(!preview || event.origin!==preview.origin || event.source!==$('site-frame').contentWindow || event.data?.type!=='drop-restorer-page') return;
  const match=state.build.pages.find(p=>p.route===event.data.route);
  if(!match) return;
  $('page-picker').value=match.route;loaded=true;$('confirmed').checked=false;controls();
});
$('site-frame').addEventListener('load',resetPreviewScroll);
$('page-picker').onchange=navigate; $('device').onchange=()=>{resize();resetPreviewScroll();$('confirmed').checked=false;controls();};$('zoom').onchange=resize;window.addEventListener('resize',resize);document.addEventListener('fullscreenchange',resize);
$('fullscreen').onclick=()=>perform(async()=>{if(document.fullscreenElement) await document.exitFullscreen();else await $('viewport').requestFullscreen();});
['open-preview','metadata-preview'].forEach(k=>$(k).onclick=()=>perform(openPreview));
$('confirmed').onchange=controls;
$('cancel').onclick=()=>perform(()=>api('cancel',{}));
$('propose').onclick=()=>perform(()=>api('action/propose',{id:id()}));
$('sample-metadata').onclick=()=>perform(()=>api('agents/metadata-sample',{id:id(),key:$('sample-page').value}));
$('sample-page').onchange=controls;
$('casino-layout-form').onsubmit=event=>{event.preventDefault();const form=event.target;const number=name=>Number(form.elements[name].value);const layout={content_width_px:number('content_width_px'),table_width_px:number('table_width_px'),row_height_px:number('row_height_px'),cell_padding_px:number('cell_padding_px'),cell_widths_px:{logo:number('cell_logo'),bonus:number('cell_bonus'),characteristics:number('cell_characteristics'),rating:number('cell_rating'),button:number('cell_button')}};perform(()=>api('action/layout',{id:id(),layout}));};
$('keep').onclick=()=>perform(async()=>{openedId=id();await api('action/continue',{id:id(),decision:'keep'});});
$('apply-meta').onclick=()=>perform(async()=>{openedId=id();await api('action/continue',{id:id(),decision:'agent'});});
$('reopen').onclick=()=>perform(async()=>{openedId=id();await api('action/reopen',{id:id()});});
$('run-checks').onclick=()=>perform(()=>api('action/checks',{id:id()}));
async function advanceOwner() {
  const build=state.build;
  if(build?.status==='metadata_review') {
    openedId=id();await api('action/continue',{id:id(),decision:'keep'});
  } else if(build?.status==='site_review') {
    openedId=id();await api('checklist/theme',{id:id(),owner_approved:true,fingerprint:preview?.stage==='site_review'?preview.fingerprint:build.checklist?.fingerprint});
  } else if(build?.status==='ready') {
    await api('package',{id:id(),receipt:preview?.receipt,digest:preview?.digest,confirmed:$('confirmed').checked,accept_findings:true});
  }
}
$('package').onclick=()=>perform(advanceOwner);
$('capture-one').onclick=()=>perform(()=>api('screenshots',{id:id(),key:state.build.pages.find(p=>p.route===$('page-picker').value).key,device:$('device').value}));
$('capture-all').onclick=()=>perform(()=>api('screenshots',{id:id()}));
$('files-button').onclick=()=>perform(async()=>{const files=await api('files',{id:id()});$('files-list').hidden=false;$('files-list').innerHTML='<h2>Файлы и снимки</h2><div class="downloads">'+files.map(f=>`<a href="${esc(f.url)}" download>${esc(f.name)}</a>`).join('')+'</div>';});
$('edit-toggle').onclick=()=>{$('editor').hidden=!$('editor').hidden;};
$('edit-parameters').onclick=()=>{const r=state.build.request, form=$('restore-form');['main_page','lang','final_domain','casino_label'].forEach(k=>form.elements[k].value=r[k]);form.elements.menu_pages.value=r.menu_pages.join('\n');r.casino_pages.forEach((p,i)=>{form.elements['title'+i].value=p.title;form.elements['slug'+i].value=p.slug;});tab('restore');notice('Параметры скопированы. Запуск создаст отдельную сборку.');};
['logo','favicon'].forEach(kind=>$(kind).onclick=()=>perform(()=>api('action/branding',{id:id(),kind,topic:kind==='favicon'?$('favicon-topic').value:'auto'})));
$('load-html').onclick=()=>perform(async()=>{const page=state.build.pages.find(p=>p.route===$('page-picker').value);const result=await api('page/'+id()+'/'+page.key);editing={key:page.key,digest:result.digest};$('html-editor').value=result.html;$('html-label').hidden=false;$('save-html').hidden=false;});
$('save-html').onclick=()=>perform(()=>api('page',{id:id(),...editing,html:$('html-editor').value}));
$('restore-form').onsubmit=event=>{event.preventDefault();perform(async()=>{const f=Object.fromEntries(new FormData(event.target)); const r={main_page:f.main_page.trim(),menu_pages:f.menu_pages.split('\n').map(x=>x.trim()).filter(Boolean),lang:f.lang.trim(),final_domain:f.final_domain.trim(),casino_label:f.casino_label,casino_pages:[0,1,2].map(i=>({title:f['title'+i],slug:f['slug'+i]}))};openedId='new';await api('restore',r);});};
$('agent-form').onsubmit=event=>{event.preventDefault();perform(async()=>{const f=Object.fromEntries(new FormData(event.target));await api('agents',{action:'connect',...f});event.target.elements.api_key.value='';notice('Агент подключён. Можно подготовить варианты метаданных или проверить подключение.');});};
$('settings-form').onsubmit=event=>{event.preventDefault();perform(async()=>{const f=Object.fromEntries(new FormData(event.target));await api('settings',{...f,auto_screens:event.target.elements.auto_screens.checked});notice('Настройки сохранены.');});};
tab(location.hash.slice(1) || 'projects');refresh(true);setInterval(()=>refresh(),1200);
