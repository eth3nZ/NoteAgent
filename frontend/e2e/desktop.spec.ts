import { test, expect, type Page } from '@playwright/test';

async function boot(page: Page, completed = false) {
  await page.addInitScript(({ completed }) => {
    const image='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5S0AAAAASUVORK5CYII=';
    const markdown='# Neural Networks\n\n## Gradient Descent\n\nA clear definition, followed by the update rule.\n\n\\[w_{t+1}=w_t-\\eta\\nabla f(w_t)\\]\n\n### Worked example\n\nThe first update gives $w_1=1.6$.\n\n```python\nmessage = """\n123\n\n\nkept  \n"""\n```\n\n<table><tr><th>Method</th><th>Purpose</th></tr><tr><td>VAE</td><td>Generation</td></tr></table>\n\n<img src="https://example.invalid/tracking"><script>window.stolen=true</script>';
    const result={task_id:'test-task',title:'Neural Networks',output_path:'/fixture/Course_notes.md',status:'completed',reason:'',page_count:12,completed_pages:Array.from({length:12},(_,i)=>i+1),cost_usd:0,retry_at:null,paid_eligible:false,revision_id:0,exported_revision_id:0,published_revision_id:0,notion:null};
    const state: any={ready:true,busy:false,closing:false,message:'Choose a document to begin.',file:completed?'/fixture/Course.pdf':'',markdown:completed?markdown:'',mode:completed?'open':'create',events:[],operation:{id:0,error:null},
      config:{provider:'openrouter',vision_model:'nvidia/nemotron-3.5-super-vl:free',gemini_model:'gemini-3.5-flash-lite',gemini_free_project:false,primary_paid:false,paid_enabled:false,paid_model:'qwen/fixture',paid_providers:'',free_providers:'nvidia',input_price:.035,output_price:1.5,paid_image_price:0,paid_request_price:0,document_limit:.1,monthly_limit:1,base_url:'https://openrouter.ai/api/v1',language:'en',output_format:'md',output_dir:'/fixture/Notes',include_source_pages:false,notion_mode:'create_child',publish_notion:false,notion_page_id:''},
      credentials:{api_key:{saved:true},gemini_api_key:{saved:false},notion_token:{saved:false},paid_api_key:{saved:false}},result:completed?result:null,estimate:null};
    const records: any[]=[];
    let rows:any[]=[{id:'test-task',document:'Course.pdf',updated:1795430000,status:'completed',page_count:12,cache_key:'fixture',body_bytes:1024,shared_bytes:4096,shared_tasks:1,protected_reason:''}];
    const startOperation=() => {state.busy=true;state.operation={id:state.operation.id+1,error:null};return {started:true,operation_id:state.operation.id};};
    const api={get_state:async()=>structuredClone(state),call:async(name:string,args:any={})=>{
      records.push({name,args});
      if(name==='pick_file'){state.file='/fixture/Course.pdf';return {file:state.file};}
      if(name==='preferences'){Object.assign(state.config,args);return {saved:true};}
      if(name==='interaction')return {};
      if(name==='estimate'){state.estimate={document:{page_count:12},cached_pages:state.result?.completed_pages.length||0,pending_pages:state.result?0:12,planned_requests:state.result?0:2,quota:{remaining_requests:40},quota_note:'Server snapshot',cost_low:null,cost_high:null,reservation:null,price_note:'',backup_enabled:false,backup_warning:'',cost_model:''};return state.estimate;}
      if(name==='models')return [{id:'nvidia/nemotron-3.5-super-vl:free',name:'Nemotron',free:true},{id:'google/gemini-3.5-flash-lite',name:'Gemini Flash Lite',free:false,input_price:.3,output_price:2.5}];
      if(name==='save_settings'){const value=startOperation();Object.assign(state.config,args.values);for(const key of Object.keys(args.secrets)){state.credentials[key]={saved:args.secrets[key].action==='replace',environment:false,unavailable:false};}setTimeout(()=>{state.busy=false;state.message='Settings saved securely.';},100);return value;}
      if(name==='start'){if(state.busy)return {error:'Wait for the current operation.'};const value=startOperation();state.message='Reading source pages 1–8…';state.result={...result,status:'running',completed_pages:[1,2,3,4,5,6,7,8],output_path:''};state.markdown=markdown;state.mode='resume';if(args.resume){setTimeout(()=>{state.result=result;state.busy=false;state.mode='open';state.message='Notes saved.';},150);}return value;}
      if(name==='cancel'){state.busy=false;state.result.status='cancelled';state.message='Cancelled; accepted pages are saved.';return {};}
      if(name==='source')return new Promise(resolve=>setTimeout(()=>resolve({page:args.page,image,note:args.page===1?'Page one only':'Page two only'}),args.page===1?200:20));
      if(name==='source_close')return {};
      if(name==='revise'){const value=startOperation();state.markdown=args.markdown;state.result.revision_id=1;state.message='Saved revision.';setTimeout(()=>{state.busy=false;},100);return value;}
      if(name==='export'){const value=startOperation();state.result.exported_revision_id=state.result.revision_id;setTimeout(()=>{state.busy=false;},50);return value;}
      if(name==='storage')return {cache_bytes:rows.length?5120:0,reclaimable_bytes:0,tasks:rows};
      if(name==='clear_cache'){const value=startOperation();rows=[];state.markdown='';state.result.completed_pages=[];state.mode='open';state.message='Local cache cleared.';setTimeout(()=>{state.busy=false;},100);return value;}
      if(name==='open_output'||name==='open_notion'||name==='open_link')return {opened:true};
      if(name==='publish'){const value=startOperation();setTimeout(()=>{state.busy=false;state.message='Done.';},50);return value;}
      return {};
    }};
    Object.assign(window,{pywebview:{api},__fixture:{state,records,markdown,result}});
  }, { completed });
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Settings',exact:true})).toBeEnabled();
}

test('focused empty state and Gemini credential setup', async ({ page }) => {
  await boot(page);
  await expect(page.getByRole('heading',{name:'Make room for the ideas.'})).toBeVisible();
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByLabel('Service',{exact:true}).selectOption('gemini');
  await page.getByLabel('Gemini API key',{exact:true}).fill('demo-google-key');
  await expect(page.getByRole('checkbox',{name:/I confirm/})).not.toBeChecked();
  await page.getByRole('checkbox',{name:/I confirm/}).check();
  await page.getByRole('button',{name:'Save settings'}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  const payload=await page.evaluate(()=> (window as any).__fixture.records.find((item:any)=>item.name==='save_settings').args);
  expect(payload.secrets.gemini_api_key.value).toBe('demo-google-key');
  expect(payload.confirm_gemini_free).toBe(true);
  expect(payload.values).not.toHaveProperty('api_key');
  expect(payload.values).not.toHaveProperty('gemini_api_key');
});

test('math, legacy tables and literal code render safely without remote requests', async ({ page }) => {
  const remote:string[]=[];page.on('request',request=>{if(!request.url().startsWith('http://127.0.0.1'))remote.push(request.url());});
  await boot(page,true);
  await expect(page.locator('.katex').first()).toBeVisible();
  await expect(page.locator('.prose table')).toContainText('VAE');
  expect(await page.locator('.prose pre code').textContent()).toContain('123\n\n\nkept  \n');
  expect(await page.evaluate(()=>(window as any).stolen)).toBeUndefined();
  expect(remote).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'../dev_artifacts/react-migration-20261126/reader.png'});
});

test('duplicate start is prevented, cancellation responds and accepted progress resumes', async ({ page }) => {
  await boot(page);
  await page.getByRole('button',{name:'Choose a document',exact:true}).click();
  await expect(page.getByRole('button',{name:'Create notes',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Create notes',exact:true}).click({clickCount:2});
  await expect(page.getByRole('button',{name:'Cancel',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>(window as any).__fixture.records.filter((item:any)=>item.name==='start').length)).toBe(1);
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  await expect(page.getByRole('button',{name:'Resume',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Resume',exact:true}).click();
  await expect(page.getByRole('button',{name:'Open notes',exact:true})).toBeVisible();
});

test('late source response is discarded and a human revision survives editing', async ({ page }) => {
  await boot(page,true);
  await page.getByRole('button',{name:'Review sources',exact:true}).click();
  await page.locator('.source-pager input').fill('2');
  await expect(page.locator('.source-excerpt')).toContainText('Page two only');
  await page.waitForTimeout(250);
  await expect(page.locator('.source-excerpt')).not.toContainText('Page one only');
  await page.getByRole('button',{name:'Close source review'}).click();
  await page.getByRole('button',{name:'More note actions'}).click();
  await page.getByRole('button',{name:'Edit notes',exact:true}).click();
  const revision='# Revised\n\n```python\nmessage="""\n123\n\n\nkept  \n"""\n```';
  await page.getByLabel('Markdown revision').fill(revision);
  await page.getByRole('button',{name:'Save revision',exact:true}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(await page.evaluate(()=>(window as any).__fixture.state.markdown)).toBe(revision);
});

test('completed current cache disappears while Open notes remains available', async ({ page }) => {
  await boot(page,true);
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('button',{name:'Storage',exact:true}).click();
  await page.getByRole('checkbox',{name:'Select available caches'}).check();
  await page.getByRole('button',{name:'Clear selected cache'}).click();
  await expect(page.getByText('No cached tasks.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Close',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Your exported notes are kept.'})).toBeVisible();
  await page.getByRole('button',{name:'Open notes',exact:true}).click();
  expect(await page.evaluate(()=>(window as any).__fixture.records.filter((item:any)=>item.name==='start').length)).toBe(0);
});

test('an uncertain Notion retry requires a separate explicit confirmation', async ({ page }) => {
  await boot(page,true);
  await page.evaluate(()=>{const value=(window as any).__fixture;value.state.result.reason='notion_failed';value.state.result.notion={page_id:'fixture',url:'https://notion.so/fixture',mode:'append'};});
  await page.waitForTimeout(300);
  await page.getByRole('button',{name:'More note actions'}).click();
  await page.getByRole('button',{name:'Retry Notion',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Publish another copy?'})).toBeVisible();
  expect(await page.evaluate(()=>(window as any).__fixture.records.filter((item:any)=>item.name==='publish').length)).toBe(0);
  await page.getByRole('button',{name:'Publish copy',exact:true}).click();
  expect(await page.evaluate(()=>(window as any).__fixture.records.find((item:any)=>item.name==='publish').args.confirm_repeat)).toBe(true);
});
