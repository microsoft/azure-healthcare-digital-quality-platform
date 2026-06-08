import React from 'react';

interface Prediction {
  name: string;
  label: string;
  score: string;
}

interface PatientStatsProps {
  predictions: Prediction[];
}

export const PatientStats: React.FC<PatientStatsProps> = ({ predictions = [] }) => {
  const getScoreIcon = (label: string) => {
    switch (label.toLowerCase()) {
      case 'high':
        return '▲';
      case 'medium':
        return '●';
      case 'low':
        return '▼';
      default:
        return '●';
    }
  };

  const getScoreColor = (label: string) => {
    switch (label.toLowerCase()) {
      case 'high':
        return 'text-red-600';
      case 'medium':
        return 'text-yellow-600';
      case 'low':
        return 'text-green-600';
      default:
        return 'text-gray-600';
    }
  };

  const formatPredictionName = (name: string) => {
    return name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  };

  if (!predictions || predictions.length === 0) {
    return (
      <div className="bg-gray-100 rounded-lg p-4 h-full flex flex-col">
        <h3 className="text-lg font-semibold text-gray-800 mb-3">Prediction Scores</h3>
        <p className="text-gray-500 text-sm">No predictions available</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-100 rounded-lg p-4 h-full flex flex-col">
      <h3 className="text-lg font-semibold text-gray-800 mb-3">Prediction Scores</h3>
      <div className="flex flex-col sm:flex-row sm:flex-wrap lg:flex-nowrap items-start gap-4 lg:gap-6 flex-1">
        {predictions.map((prediction, index) => (
          <div key={index} className="flex flex-col items-start gap-1 lg:flex-1 min-w-0">
            <div className="font-medium text-sm text-gray-700 whitespace-nowrap overflow-hidden text-ellipsis w-full">
              {formatPredictionName(prediction.name)}
            </div>
            <div className={`flex items-center gap-1 ${getScoreColor(prediction.label)}`}>
              <span className="text-sm font-medium">{getScoreIcon(prediction.label)}</span>
              <span className="text-sm font-medium">{prediction.label.toUpperCase()}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default PatientStats;
