from flask import Flask, render_template, request, jsonify
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import requests
import logging
import json
import polyline
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize geocoder
geocoder = Nominatim(
    user_agent="cyber_path_finder",
    timeout=10,
    domain='nominatim.openstreetmap.org'
)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search_location', methods=['POST'])
def search_location():
    try:
        query = request.json.get('query')
        if not query or len(query) < 3:  # Only search if query is 3+ characters
            return jsonify({'results': []})

        # Search for location with better parameters
        locations = geocoder.geocode(
            query,
            exactly_one=False,
            limit=5,
            addressdetails=True,
            language='en'
        )
        
        if not locations:
            return jsonify({'results': []})

        # Format results with better details
        results = []
        for loc in locations:
            # Get address details
            address = loc.raw.get('address', {})
            # Create a formatted address
            formatted_address = []
            
            # Add primary location name
            if address.get('city'):
                formatted_address.append(address['city'])
            elif address.get('town'):
                formatted_address.append(address['town'])
            elif address.get('village'):
                formatted_address.append(address['village'])
            
            # Add state/country
            if address.get('state'):
                formatted_address.append(address['state'])
            if address.get('country'):
                formatted_address.append(address['country'])

            # Create main and secondary text
            main_text = formatted_address[0] if formatted_address else loc.address.split(',')[0]
            secondary_text = ', '.join(formatted_address[1:]) if len(formatted_address) > 1 else ''

            results.append({
                'name': loc.address,
                'main_text': main_text,
                'secondary_text': secondary_text,
                'lat': loc.latitude,
                'lng': loc.longitude,
                'type': loc.raw.get('type'),
                'importance': loc.raw.get('importance', 0)
            })

        # Sort by importance
        results.sort(key=lambda x: x['importance'], reverse=True)

        return jsonify({'results': results})

    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        return jsonify({'error': 'Search failed', 'details': str(e)}), 500

@app.route('/find_path', methods=['POST'])
def find_path():
    try:
        data = request.json
        start = data.get('start')
        end = data.get('end')

        logger.info(f"Received route request - Start: {start}, End: {end}")

        # Handle text input if coordinates aren't provided
        if isinstance(start, str):
            if ',' in start:
                # Parse coordinates
                start_parts = start.split(',')
                start_lat = float(start_parts[0].strip())
                start_lon = float(start_parts[1].strip())
                # OpenRouteService expects [longitude, latitude]
                start_coords = [start_lon, start_lat]
            else:
                # Geocode address
                start_loc = geocoder.geocode(start)
                if not start_loc:
                    return jsonify({'error': 'Start location not found'}), 404
                start_coords = [start_loc.longitude, start_loc.latitude]

        if isinstance(end, str):
            if ',' in end:
                # Parse coordinates
                end_parts = end.split(',')
                end_lat = float(end_parts[0].strip())
                end_lon = float(end_parts[1].strip())
                # OpenRouteService expects [longitude, latitude]
                end_coords = [end_lon, end_lat]
            else:
                # Geocode address
                end_loc = geocoder.geocode(end)
                if not end_loc:
                    return jsonify({'error': 'End location not found'}), 404
                end_coords = [end_loc.longitude, end_loc.latitude]

        logger.info(f"Processed coordinates - Start: {start_coords}, End: {end_coords}")
        
        # OpenRouteService API request
        url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"  # Updated URL
        headers = {
            'Authorization': 'YOUR_API_KEY',
            'Content-Type': 'application/json'
        }
        
        body = {
            "coordinates": [start_coords, end_coords],
            "instructions": True,
            "preference": "recommended",
            "units": "km",
            "geometry": "true",
            "geometry_simplify": "false",
            "language": "en"
        }
        
        logger.info(f"Sending request to OpenRouteService: {body}")
        
        response = requests.post(url, json=body, headers=headers)
        logger.info(f"OpenRouteService response status: {response.status_code}")
        logger.info(f"OpenRouteService response: {response.text}")
        
        if response.status_code != 200:
            logger.error(f"OpenRouteService error: {response.text}")
            return jsonify({'error': 'Route calculation failed'}), 500
            
        route_data = response.json()
        
        if 'features' in route_data and len(route_data['features']) > 0:
            # Extract route geometry
            geometry = route_data['features'][0]['geometry']['coordinates']
            decoded_coords = [[coord[1], coord[0]] for coord in geometry]
            
            # Get route properties
            properties = route_data['features'][0]['properties']
            segments = properties.get('segments', [{}])[0]
            
            # Calculate total distance in kilometers
            total_distance = segments.get('distance', 0)  # This is in meters
            total_duration = segments.get('duration', 0)  # This is in seconds
            
            # Convert to kilometers and minutes
            total_distance_km = total_distance / 1000.0
            total_duration_min = total_duration / 60.0
            
            # Process step distances more carefully
            steps = segments.get('steps', [])
            instructions = []
            cumulative_distance = 0
            
            for step in steps:
                step_distance = step.get('distance', 0) / 1000.0  # Convert to km
                
                # Only include steps with meaningful distance
                if step_distance >= 0.1:  # More than 100 meters
                    instructions.append({
                        'instruction': step['instruction'],
                        'distance': step_distance,
                        'duration': step['duration'] / 60.0
                    })
                    cumulative_distance += step_distance
                else:
                    # For very short steps, combine with next step if possible
                    if instructions and step['instruction']:
                        last_instruction = instructions[-1]
                        last_instruction['instruction'] += f" and {step['instruction'].lower()}"
                        last_instruction['distance'] += step_distance
                        last_instruction['duration'] += step['duration'] / 60.0
                    else:
                        instructions.append({
                            'instruction': step['instruction'],
                            'distance': step_distance,
                            'duration': step['duration'] / 60.0
                        })
            
            # Calculate backup distance using coordinates
            coord_distance = 0
            for i in range(len(decoded_coords) - 1):
                point1 = decoded_coords[i]
                point2 = decoded_coords[i + 1]
                coord_distance += geodesic(point1, point2).kilometers
            
            # Use the larger of the two distances
            final_distance = max(total_distance_km, coord_distance)
            
            # Adjust step distances proportionally if needed
            if cumulative_distance > 0:
                scale_factor = final_distance / cumulative_distance
                for instruction in instructions:
                    instruction['distance'] *= scale_factor
            
            # Log distances for debugging
            logger.info(f"API distance: {total_distance_km:.3f} km")
            logger.info(f"Calculated distance: {coord_distance:.3f} km")
            logger.info(f"Final distance: {final_distance:.3f} km")
            logger.info(f"Step distances: {[f'{step['distance']:.3f}' for step in instructions]} km")
            
            return jsonify({
                'success': True,
                'route': decoded_coords,
                'distance': final_distance,
                'duration': total_duration_min,
                'instructions': instructions
            })
        else:
            logger.error("No route found in response")
            return jsonify({'error': 'No route found'}), 404

    except Exception as e:
        logger.error(f"Error in find_path: {str(e)}")
        return jsonify({'error': f'Failed to calculate route: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
