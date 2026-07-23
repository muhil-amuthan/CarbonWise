# 🌍 CarbonWise — Grid Carbon Intensity Advisor

> A real-time carbon intensity estimation system that helps users understand the environmental impact of electricity consumption by estimating the carbon intensity (gCO₂/kWh) of the power grid based on the electricity generation mix.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-Web%20App-red)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 📖 Overview

CarbonWise is an AI-powered sustainability application that estimates the **grid carbon intensity** using electricity generation data from multiple energy sources such as:

- ☀️ Solar
- 🌬️ Wind
- ⚡ Hydro
- 🔥 Coal
- ⛽ Natural Gas
- ☢️ Nuclear

The application calculates the weighted average carbon intensity and provides actionable recommendations to reduce emissions.

---

## ✨ Features

- 📊 Real-time carbon intensity estimation
- ⚡ Supports multiple power generation sources
- 🌱 Calculates weighted CO₂ emissions (gCO₂/kWh)
- 📈 Interactive visualizations
- 💡 Personalized recommendations
- 📥 CSV input support
- 📋 Summary report generation
- 🌐 Streamlit web interface

---

## 🛠️ Tech Stack

### Frontend
- Streamlit

### Backend
- Python

### Data Processing
- Pandas
- NumPy

### Visualization
- Matplotlib
- Plotly

### Machine Learning (Optional)
- Scikit-learn

---

## 📂 Project Structure

```
CarbonWise/
│
├── data/                     # Input datasets
├── docs/                     # Documentation
├── src/                      # Source code
│   ├── calculator.py
│   ├── advisor.py
│   ├── visualizer.py
│   └── utils.py
│
├── app.py                    # Main Streamlit application
├── generate_summary.py       # Report generator
├── requirements.txt
├── README.md
└── LICENSE
```

---

## 🚀 Installation

Clone the repository

```bash
git clone https://github.com/your-username/CarbonWise.git
```

Move into the project

```bash
cd CarbonWise
```

Install dependencies

```bash
pip install -r requirements.txt
```

Run the application

```bash
streamlit run app.py
```

---

## 📊 How It Works

1. Load electricity generation data.
2. Calculate the contribution of each energy source.
3. Multiply each source by its emission factor.
4. Compute the weighted average carbon intensity.
5. Display results with graphs and recommendations.

---

## 📈 Example Workflow

```
Input Data
      │
      ▼
Energy Mix Calculation
      │
      ▼
Emission Factor Calculation
      │
      ▼
Carbon Intensity (gCO₂/kWh)
      │
      ▼
Visualization
      │
      ▼
Recommendations
```

---

## 📸 Screenshots

Add screenshots of your application here.

```
docs/images/dashboard.png
docs/images/results.png
```

---

## 🎯 Future Enhancements

- Live Grid API integration
- Machine Learning prediction model
- Hourly carbon intensity forecasting
- User authentication
- Historical trend analysis
- Mobile application
- Carbon savings calculator

---

## 🤝 Contributing

Contributions are welcome!

1. Fork the repository
2. Create a new branch
3. Commit your changes
4. Push to your branch
5. Create a Pull Request

---

## 📜 License

This project is licensed under the MIT License.

---

## 👨‍💻 Author

**Muhil Amuthan M**

Electronics and Communication Engineering Student

Machine Learning & Full Stack Development Enthusiast

GitHub: https://github.com/muhil-amuthan

LinkedIn:https://www.linkedin.com/in/muhil-amuthan-m

---

## ⭐ Support

If you found this project useful, consider giving it a ⭐ on GitHub.
