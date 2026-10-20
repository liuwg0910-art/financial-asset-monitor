/* Static multi-page navigation and compatibility with previous section links. */
const pageFile = path => path.split('/').filter(Boolean).pop() || 'index.html';
const currentPage = pageFile(location.pathname);
if(currentPage==='index.html' && ['#alertsCenter','#guide'].includes(location.hash)) {
  location.replace(location.hash==='#alertsCenter' ? 'alerts.html' : 'data.html');
}
function updatePageNavigation() {
  document.querySelectorAll('.side-nav a').forEach(link=>{
    const target=new URL(link.getAttribute('href'),location.href);
    const targetFile=pageFile(target.pathname);
    const currentFile=pageFile(location.pathname);
    const active=currentFile===targetFile && (currentFile!=='index.html' || (location.hash==='#watchlist' ? target.hash==='#watchlist' : !target.hash));
    link.classList.toggle('nav-active',active);
    if(active) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
}
document.addEventListener('DOMContentLoaded',updatePageNavigation);
window.addEventListener('hashchange',updatePageNavigation);
