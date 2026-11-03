const status = document.querySelector('#status');
let session = localStorage.getItem('maa_session') || '';
async function refreshDevices() {
  if (!session) return;
  const response = await fetch('/api/v1/devices', {headers:{Authorization:`Bearer ${session}`}});
  if (!response.ok) return;
  const list = await response.json();
  document.querySelector('#devices').innerHTML = list.devices.map(d => `<option value="${d.id}">${d.name}${d.revoked ? '（已撤销）' : ''}</option>`).join('');
}
async function refresh() {
  if (!session) return;
  const device = document.querySelector('#device').value.trim(); if (!device) return;
  const response = await fetch(`/api/v1/devices/${encodeURIComponent(device)}/snapshot`, {headers:{Authorization:`Bearer ${session}`}});
  if (response.ok) document.querySelector('#snapshot').textContent = JSON.stringify(await response.json(), null, 2);
}
document.querySelector('#login').addEventListener('click', async () => {
  const response = await fetch('/api/v1/session', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:document.querySelector('#password').value})});
  const data = await response.json();
  if (!response.ok) { status.textContent = '登录失败'; return; }
  session = data.token; localStorage.setItem('maa_session', session); status.textContent = '已登录';
  refreshDevices(); refresh();
});
document.querySelector('#device').addEventListener('change', refresh);
document.querySelectorAll('button[data-kind]').forEach(btn => btn.addEventListener('click', async () => {
  if (!session) { status.textContent = '请先登录'; return; }
  const device = document.querySelector('#device').value.trim();
  if (!device) { status.textContent = '请填写设备 ID'; return; }
  const body = {kind:btn.dataset.kind, args:{}, idempotency_key:crypto.randomUUID(), expires_at:Math.floor(Date.now()/1000)+900};
  const response = await fetch(`/api/v1/devices/${encodeURIComponent(device)}/commands`, {method:'POST', headers:{'Content-Type':'application/json', Authorization:`Bearer ${session}`}, body:JSON.stringify(body)});
  const data = await response.json(); status.textContent = response.ok ? `已排队：${data.command_id}` : `提交失败：${data.error || response.status}`;
}));
document.querySelector('#save-patch').addEventListener('click', async () => {
  try {
    const device = document.querySelector('#device').value.trim(); const snapshot = JSON.parse(document.querySelector('#snapshot').textContent);
    const body = {kind:'patch_config', args:{base_revision:Number(snapshot.revision || 0), patch:JSON.parse(document.querySelector('#patch').value)}, idempotency_key:crypto.randomUUID(), expires_at:Math.floor(Date.now()/1000)+3600};
    const response = await fetch(`/api/v1/devices/${encodeURIComponent(device)}/commands`, {method:'POST', headers:{'Content-Type':'application/json', Authorization:`Bearer ${session}`}, body:JSON.stringify(body)});
    const data = await response.json(); status.textContent = response.ok ? `配置补丁已排队：${data.command_id}` : `保存失败：${data.error || response.status}`;
  } catch (error) { status.textContent = `补丁格式错误：${error.message}`; }
});
