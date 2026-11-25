const {chromium} = require('playwright');

(async () => {
    const browser = await chromium.launch({headless: true, channel: 'msedge'});
    try {
        const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto('http://127.0.0.1:8765/');
        await page.getByRole('button', {name: '接诊此患者'}).click();
        await page.waitForURL('**/clinic');
        await page.getByRole('button', {name: '出院并结束本局'}).waitFor();
        await page.waitForFunction(() => document.querySelector('#clock').textContent.includes('0 分钟'));
        if (await page.getByRole('button', {name: '确认并推进至完成'}).count()) {
            throw Error('Old confirmation button remains');
        }
        const baseline = await page.evaluate(async () => {
            const id = localStorage.getItem('clinic-session');
            const response = await fetch('/api/simulation', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id})
            });
            return response.json();
        });
        let snapshot = baseline;
        await page.route('**/api/simulation', route => route.fulfill({json: snapshot}));
        let submissions = 0;
        await page.route('**/api/submit', async route => {
            submissions++;
            snapshot = {...snapshot, revision: 1, job: {id: 'ui-fixture', status: 'complete', message: '已安排测试检查'},
                queue: [{id: 'test', label: '测试检查', sampleMinute: 10, readyMinute: 20, status: 'scheduled'}]};
            await route.fulfill({status: 202, json: snapshot});
        });
        await page.locator('#input').fill('测试检查');
        await page.getByRole('button', {name: '提交行动', exact: true}).click();
        await page.getByText('第 20 分钟报告', {exact: false}).waitFor();
        if (submissions !== 1) throw Error('Action did not submit directly exactly once');
        await page.route('**/api/discharge', async route => {
            snapshot = {...snapshot, revision: 2, outcome: {
                status: 'discharged', title: '界面测试后日谈', narrative: '这里是界面测试文本。'
            }};
            await route.fulfill({status: 202, json: snapshot});
        });
        await page.screenshot({path: 'timeline-preview.png', fullPage: true});
        await page.getByRole('button', {name: '出院并结束本局'}).click();
        await page.getByText('界面测试后日谈', {exact: true}).waitFor();
        if (errors.length) throw Error(errors.join('\n'));
        console.log('PASS: admission, direct action, queue, discharge dialog. Action/story use UI fixtures.');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
