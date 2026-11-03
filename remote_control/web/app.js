const status = document.querySelector('#status');
let session = localStorage.getItem('maa_session') || '';
document.querySelector('#login').addEventListener('click', async () => {
  const response = await fetch('/api/v1/session', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:document.querySelector('#password').value})});
  const data = await response.json();
  if (!response.ok) { status.textContent = '登录失败'; return; }
  session = data.token; localStorage.setItem('maa_session', session); status.textContent = '已登录';
});
document.querySelectorAll('button[data-kind]').forEach(btn => btn.addEventListener('click', async () => {
  if (!session) { status.textContent = '请先登录'; return; }
  const device = document.querySelector('#device').value.trim();
  if (!device) { status.textContent = '请填写设备 ID'; return; }
  const body = {kind:btn.dataset.kind, args:{}, idempotency_key:crypto.randomUUID(), expires_at:Math.floor(Date.now()/1000)+900};
  const response = await fetch(`/api/v1/devices/${encodeURIComponent(device)}/commands`, {method:'POST', headers:{'Content-Type':'application/json', Authorization:`Bearer ${session}`}, body:JSON.stringify(body)});
  const data = await response.json(); status.textContent = response.ok ? `已排队：${data.command_id}` : `提交失败：${data.error || response.status}`;
}));
