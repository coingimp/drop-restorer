function drSetOpen(host, opened) {
  host.classList.toggle('dr-open', opened);
  if (!opened) delete host.dataset.drHoverOpen;
  const button = host.querySelector(':scope > button');
  if (button) button.setAttribute('aria-expanded', String(opened));
}
document.addEventListener('click', function (event) {
  const mobile = event.target.closest('.dr-mobile-toggle');
  const casino = event.target.closest('.dr-casino-toggle');
  const button = mobile || casino;
  if (button) {
    event.preventDefault();
    const host = button.parentElement;
    const opened = casino && host.dataset.drHoverOpen === 'true' ? true : !host.classList.contains('dr-open');
    delete host.dataset.drHoverOpen;
    drSetOpen(host, opened);
  } else if (!event.target.closest('.dr-casino')) {
    document.querySelectorAll('.dr-casino.dr-open').forEach(function (host) {
      drSetOpen(host, false);
    });
  }
  if (!event.target.closest('.dr-navigation')) {
    document.querySelectorAll('.dr-navigation.dr-open').forEach(host => drSetOpen(host, false));
  }
  const slideButton = event.target.closest('[data-dr-slide]');
  if (slideButton) {
    const carousel = slideButton.closest('#hp-carousel');
    carousel.querySelectorAll('.dr-slide').forEach((slide, index) => {slide.hidden = index !== Number(slideButton.dataset.drSlide);});
    carousel.querySelectorAll('[data-dr-slide]').forEach(button => button.setAttribute('aria-pressed', String(button === slideButton)));
  }
});
document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') document.querySelectorAll('.dr-open').forEach(function (host) {
    const button = host.querySelector(':scope > button');
    if (host.contains(document.activeElement) && button) button.focus();
    drSetOpen(host, false);
  });
});
document.querySelectorAll('.dr-casino').forEach(function (host) {
  host.addEventListener('mouseenter', () => {
    if (matchMedia('(min-width:801px) and (hover:hover)').matches && !host.classList.contains('dr-open')) {
      host.dataset.drHoverOpen = 'true';
      drSetOpen(host, true);
    }
  });
  host.addEventListener('mouseleave', () => {if (matchMedia('(min-width:801px) and (hover:hover)').matches) drSetOpen(host, false);});
});
matchMedia('(max-width:800px)').addEventListener('change', () => {
  document.querySelectorAll('.dr-open').forEach(host => drSetOpen(host, false));
});
