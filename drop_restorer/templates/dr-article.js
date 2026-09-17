(function () {
  'use strict';
  const lang = (document.documentElement.lang || 'en').slice(0, 2).toLowerCase();
  const labels = {cs: 'Tabulka', sk: 'Tabuľka', en: 'Table', de: 'Tabelle', pl: 'Tabela', ru: 'Таблица', uk: 'Таблиця', fr: 'Tableau', es: 'Tabla', it: 'Tabella', pt: 'Tabela'};
  const contents = {cs: 'Obsah článku', sk: 'Obsah článku', en: 'On this page', de: 'Inhaltsverzeichnis', pl: 'Spis treści', ru: 'Содержание статьи', uk: 'Зміст статті', fr: 'Sommaire', es: 'Contenido', it: 'Indice', pt: 'Índice'};
  document.querySelectorAll('.dr-article').forEach(article => {
    article.querySelectorAll('table').forEach(table => {
      if (table.closest('.dr-casino-table, .dr-article-table')) return;
      let wrapper = table.parentElement;
      if (!wrapper.matches('figure.wp-block-table')) {
        wrapper = document.createElement('div');
        table.before(wrapper);
        wrapper.append(table);
      }
      wrapper.classList.add('dr-article-table');
      wrapper.setAttribute('role', 'region');
      wrapper.setAttribute('aria-label', table.caption?.textContent.trim() || labels[lang] || labels.en);
      if ([...table.rows].some(row => [...row.cells].reduce((n, cell) => n + cell.colSpan, 0) >= 3)) table.dataset.drWide = '';
      const focus = () => { wrapper.tabIndex = wrapper.scrollWidth > wrapper.clientWidth + 1 ? 0 : -1; };
      focus();
      if ('ResizeObserver' in window) new ResizeObserver(focus).observe(wrapper);
      else window.addEventListener('resize', focus);
    });
    if (article.dataset.drToc === 'off' || article.querySelector('.dr-article-toc, .wp-block-table-of-contents, #ez-toc-container, .lwptoc, .rank-math-toc-block')) return;
    const headings = [...article.querySelectorAll('h2')].filter(h => h.textContent.trim() && !h.closest('.dr-casino-table, nav, aside, details, [hidden]'));
    if (headings.length < 3) return;
    const headingIds = new Set(headings.map(h => h.id).filter(Boolean));
    // Preserve a hand-written or plugin-provided contents list.
    if ([...article.querySelectorAll('a[href^="#"]')].filter(a => headingIds.has(a.getAttribute('href').slice(1))).length >= 3) return;
    const used = new Set([...document.querySelectorAll('[id]')].map(e => e.id));
    const panel = document.createElement('details');
    panel.className = 'dr-article-toc';
    panel.open = window.matchMedia('(min-width: 801px)').matches && headings.length <= 8;
    const summary = document.createElement('summary');
    summary.textContent = contents[lang] || contents.en;
    const nav = document.createElement('nav');
    nav.setAttribute('aria-label', summary.textContent);
    const list = document.createElement('ol');
    headings.forEach((heading, index) => {
      if (!heading.id) {
        const slug = heading.textContent.trim().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').slice(0, 80);
        const base = 'dr-section-' + (slug || (index + 1));
        let id = base, suffix = 2;
        while (used.has(id)) id = base + '-' + suffix++;
        heading.id = id;
        used.add(id);
      }
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.href = '#' + encodeURIComponent(heading.id);
      link.textContent = heading.textContent.trim();
      item.append(link);
      list.append(item);
    });
    nav.append(list);
    panel.append(summary, nav);
    headings[0].before(panel);
  });
}());
