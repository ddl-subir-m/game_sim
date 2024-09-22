const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const gpt4Farm = document.getElementById('gpt4-farm');
const gpt35Farm = document.getElementById('gpt35-farm');

let moneyChart, energyChart, actionChart;
let gpt4Data = { money: [], energy: [], actions: [] };
let gpt35Data = { money: [], energy: [], actions: [] };

const cropEmojis = {
    'Maintenance': '🛠️',
    'Harvest': '🧺',
    'Plant': '🌱',
    'Corn': '🌽',
    'Wheat': '🌾',
    'Tomato': '🍅'
};

function initCharts() {
    const ctx1 = document.getElementById('moneyChart').getContext('2d');
    const ctx2 = document.getElementById('energyChart').getContext('2d');
    const ctx3 = document.getElementById('actionChart').getContext('2d');

    moneyChart = new Chart(ctx1, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                { label: 'GPT-3.5 Money', data: [], borderColor: 'blue', fill: false },
                { label: 'GPT-4 Money', data: [], borderColor: 'red', fill: false }
            ]
        },
        options: { responsive: true, title: { display: true, text: 'Money Over Time' } }
    });

    energyChart = new Chart(ctx2, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                { label: 'GPT-3.5 Energy', data: [], borderColor: 'green', fill: false },
                { label: 'GPT-4 Energy', data: [], borderColor: 'orange', fill: false }
            ]
        },
        options: { responsive: true, title: { display: true, text: 'Energy Over Time' } }
    });

    const actionAbbreviations = {
        'Plant': 'PL',
        'Harvest': 'HV',
        'Maintenance': 'MT',
        'Sell': 'SL',
        'Buy': 'BY',
        'Sabotage': 'SB'
    };

    const abbreviationArray = Object.values(actionAbbreviations);

    // Reverse mapping for tooltips
    const abbreviationToAction = Object.fromEntries(
        Object.entries(actionAbbreviations).map(([key, value]) => [value, key])
    );

    actionChart = new Chart(ctx3, {
        type: 'scatter',
        data: {
            datasets: [
                { label: 'GPT-3.5 Actions', data: [], pointStyle: [], backgroundColor: 'blue' },
                { label: 'GPT-4 Actions', data: [], pointStyle: [], backgroundColor: 'red' }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            height: 300,  // Set a fixed height
            title: { display: true, text: 'Actions Over Time' },
            scales: {
                x: { type: 'linear', position: 'bottom', title: { display: true, text: 'Day' } },
                y: { 
                    type: 'category', 
                    labels: abbreviationArray,
                    ticks: {
                        callback: function(value, index) {
                            return abbreviationArray[index];
                        }
                    }
                }
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return `Day ${context.parsed.x}: ${context.raw.action}`;
                        },
                        title: function(tooltipItems) {
                            const abbreviation = tooltipItems[0].raw.y;
                            return `${abbreviation} - ${abbreviationToAction[abbreviation]}`;
                        }
                    }
                }
            }
        }
    });

    // Store the actionAbbreviations in the chart for later use
    actionChart.actionAbbreviations = actionAbbreviations;
}

function updateFarmGrid(farm, crops, lastAction) {
    const grid = farm.querySelector('.farm-grid');
    grid.innerHTML = '';
    
    // Create a maintenance overlay
    const overlay = document.createElement('div');
    overlay.className = 'maintenance-overlay';
    overlay.textContent = cropEmojis['Maintenance'];
    overlay.style.display = 'none';
    grid.appendChild(overlay);

    for (let i = 0; i < 25; i++) {
        const cell = document.createElement('div');
        if (i < crops.length) {
            const cropType = crops[i].type;
            cell.textContent = cropEmojis[cropType] || cropType;
            cell.title = `${cropType} (planted on day ${crops[i].planted_at})`;
        } else {
            cell.textContent = '';
            cell.title = 'Empty plot';
        }
        grid.appendChild(cell);
    }

    // Show overlay if last action was Maintenance
    if (lastAction === 'Maintenance') {
        overlay.style.display = 'flex';
    }
}

function updateFarmStats(farm, data) {
    farm.querySelector('.day').textContent = data.day;
    farm.querySelector('.money').textContent = data.money;
    farm.querySelector('.energy').textContent = data.energy;
}

function updateCharts(day) {
    moneyChart.data.labels.push(day);
    moneyChart.data.datasets[0].data.push(gpt35Data.money[gpt35Data.money.length - 1]);
    moneyChart.data.datasets[1].data.push(gpt4Data.money[gpt4Data.money.length - 1]);
    moneyChart.update();

    energyChart.data.labels.push(day);
    energyChart.data.datasets[0].data.push(gpt35Data.energy[gpt35Data.energy.length - 1]);
    energyChart.data.datasets[1].data.push(gpt4Data.energy[gpt4Data.energy.length - 1]);
    energyChart.update();
}

function updateActionChart(day, gpt35Action, gpt4Action) {
    console.log('Updating action chart:', day, gpt35Action, gpt4Action);

    const gpt35ActionType = gpt35Action.split(' ')[1];
    const gpt4ActionType = gpt4Action.split(' ')[1];

    // Function to get the abbreviation of the action type
    const getActionAbbreviation = (actionType) => actionChart.actionAbbreviations[actionType] || '';

    // Only add data points if the action is not "finished"
    if (gpt35ActionType !== 'finished') {
        actionChart.data.datasets[0].data.push({ 
            x: day, 
            y: getActionAbbreviation(gpt35ActionType), 
            action: gpt35Action,
            id: `gpt35-${day}`
        });
        actionChart.data.datasets[0].pointStyle.push(cropEmojis[gpt35ActionType] || '❓');
    }

    if (gpt4ActionType !== 'finished') {
        actionChart.data.datasets[1].data.push({ 
            x: day, 
            y: getActionAbbreviation(gpt4ActionType), 
            action: gpt4Action,
            id: `gpt4-${day}`
        });
        actionChart.data.datasets[1].pointStyle.push(cropEmojis[gpt4ActionType] || '❓');
    }

    console.log('Chart data:', actionChart.data);

    actionChart.update();
}

// Add this function at the top level of the script
function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

let eventSource; // Declare this at the top of your script

startBtn.addEventListener('click', async () => {
    startBtn.disabled = true;
    stopBtn.disabled = false;

    // Start the competition on the server
    // await fetch('/start-competition', { method: 'POST' });

    eventSource = new EventSource('/stream-competition');

    eventSource.onmessage = async (event) => {
        const data = JSON.parse(event.data);
        
        await delay(1000); // Add a 1-second delay between updates

        updateFarmGrid(gpt35Farm, data.gpt35.crops, data.gpt35.decision.split(' ')[1]);
        updateFarmGrid(gpt4Farm, data.gpt4.crops, data.gpt4.decision.split(' ')[1]);

        updateFarmStats(gpt35Farm, data.gpt35);
        updateFarmStats(gpt4Farm, data.gpt4);

        gpt35Data.money.push(data.gpt35.money);
        gpt35Data.energy.push(data.gpt35.energy);
        gpt4Data.money.push(data.gpt4.money);
        gpt4Data.energy.push(data.gpt4.energy);

        updateCharts(data.gpt4.day);
        updateActionChart(data.gpt4.day, data.gpt35.decision, data.gpt4.decision);
    };

    eventSource.onerror = () => {
        eventSource.close();
        startBtn.disabled = false;
        stopBtn.disabled = true;
    };
});

stopBtn.addEventListener('click', async () => {
    try {
        const response = await fetch('/stop-competition', { method: 'POST' });
        if (response.ok) {
            startBtn.disabled = false;
            stopBtn.disabled = true;
            // Close the event source to stop receiving updates
            if (eventSource) {
                eventSource.close();
            }
        } else {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
    } catch (error) {
        console.error("Failed to stop competition:", error);
    }
});

document.addEventListener('DOMContentLoaded', () => {
    initCharts();
});