/* The directory owns the outer navigation, including edit/return flows. */
(() => {
    let embedded = new URLSearchParams(location.search).get('embedded') === '1';
    try { embedded ||= window.parent !== window && window.frameElement?.id === 'reader'; } catch (_) {}
    if (!embedded) return;
    document.documentElement.classList.add('embedded-reader');
    const style = document.createElement('style');
    style.textContent = '.embedded-reader .system-container>.sidebar,.embedded-reader .knowledge-book-sidebar{display:none!important}.embedded-reader .system-container{display:block!important}.embedded-reader .main-content{margin-left:0!important;width:100%!important;padding:0!important}';
    document.head.append(style);
    const current = new URL(location.href);
    current.searchParams.set('embedded', '1');
    history.replaceState(null, '', current);
    document.addEventListener('click', event => {
        const link = event.target.closest('a[href]');
        if (!link || link.target === '_blank') return;
        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin) return;
        if (url.pathname.endsWith('/main.html')) {
            event.preventDefault();
            window.top.location.href = url.href;
        } else if (/\/(document-detail|edit-document|knowledge-versions)\.html$/.test(url.pathname)) {
            url.searchParams.set('embedded', '1'); link.href = url.href;
        }
    }, true);
})();
