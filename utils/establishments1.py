from flask import Flask, request, jsonify
import googlemaps
import os
from collections import Counter
import requests
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

# Constants
TYPE_PRIORITY = [
    'restaurant', 'cafe', 'bar',
    'supermarket', 'convenience_store', 'bakery',
    'pharmacy', 'hospital', 'doctor',
    'bank', 'atm', 'finance',
    'school', 'university',
    'store', 'point_of_interest', 'establishment'
]

@dataclass
class Address:
    barangay: str
    municipality: str
    province: str
    region: str

class GoogleMapsService:
    def __init__(self):
        api_key = os.getenv('GOOGLE_PLACES_API_KEY')
        print(api_key)
        
        if not api_key:
            raise ValueError("Google Maps API key not found in environment variables")
        self.client = googlemaps.Client(key=api_key)

    def get_nearby_places(self, latitude: float, longitude: float, radius: int) -> List[Dict]:
        """
        Get all nearby places using pagination to fetch maximum results.
        
        Args:
            latitude (float): The latitude coordinate
            longitude (float): The longitude coordinate
            radius (int): Search radius in meters
            
        Returns:
            List[Dict]: List of all nearby places
        """
        try:
            all_results = []
            # Initial request
            response = self.client.places_nearby(
                location=(latitude, longitude),
                radius=radius
            )
            
            # Add initial results
            all_results.extend(response.get('results', []))
            
            # Continue fetching while there's a next page token
            while 'next_page_token' in response:
                # Wait for the token to become valid (Google requires a short delay)
                time.sleep(2)
                
                # Get next page of results
                response = self.client.places_nearby(
                    location=(latitude, longitude),
                    radius=radius,
                    page_token=response['next_page_token']
                )
                
                # Add new results
                all_results.extend(response.get('results', []))
                
                # Break if we've reached the maximum number of pages (typically 3)
                if len(all_results) >= 60:  # Google Places API typically returns 20 results per page
                    break
            print(all_results[:3])
            return all_results
        except Exception as e:
            raise Exception(f"Failed to fetch nearby places: {str(e)}")
            
    def get_address_components(self, latitude: float, longitude: float) -> Dict:
        """
        Get detailed address components including municipality using reverse geocoding.
        
        Args:
            latitude (float): The latitude coordinate
            longitude (float): The longitude coordinate
            
        Returns:
            Dict: Address components including municipality and other details
        """
        try:
            result = self.client.reverse_geocode((latitude, longitude))
            if not result:
                return {}
                
            address_components = {}
            for component in result[0]['address_components']:
                types = component['types']
                if 'locality' in types:
                    address_components['municipality'] = component['long_name']
                elif 'administrative_area_level_2' in types:
                    address_components['province'] = component['long_name']
                elif 'administrative_area_level_1' in types:
                    address_components['region'] = component['long_name']
                elif 'sublocality_level_1' in types:
                    address_components['barangay'] = component['long_name']
                    
            return address_components
        except Exception as e:
            raise Exception(f"Failed to get address components: {str(e)}")

class Establishments:
    def __init__(self, latitude: float, longitude: float, business_type: str, address: Address, description: str = "", radius: int = 2000):
        """
        Initialize the Establishments class and fetch all necessary data.
        
        Args:
            latitude (float): The latitude of the establishment
            longitude (float): The longitude of the establishment
            business_type (str): The type of business
            address (Address): The address of the establishment
            description (str): Description of the business
            radius (int): Search radius in meters for nearby establishments
        """
        self.latitude = latitude
        self.longitude = longitude
        self.business_type = business_type
        self.address = address
        self.description = description
        self.maps_service = GoogleMapsService()
        
        # Initialize data structures
        self.nearby_establishments: List[Dict] = []
        self.competitors: List[Dict] = []
        self.best_types_summary: Tuple[List[Dict], Dict] = ([], {})
        self.location_details: Dict = {}
        self.other_establishments: List[Dict] = []
        
        # Fetch all data during initialization
        self._fetch_all_data(radius)

    def _fetch_all_data(self, radius: int) -> None:
        """
        Fetch all necessary data for the establishment.
        
        Args:
            radius (int): Search radius in meters
        """
        try:
            # Get nearby establishments with pagination
            nearby_results = self.maps_service.get_nearby_places(self.latitude, self.longitude, radius)
            self.nearby_establishments = [
                self._parse_place_data(place) 
                for place in nearby_results
                if self._parse_place_data(place) is not None
            ]
            
            # Find competitors
            self.find_competitors()
            
            # Get best types summary
            self.best_types_summary = self.get_best_types_summary()
            
            # Get location details
            self.location_details = self.maps_service.get_address_components(self.latitude, self.longitude)
            
        except Exception as e:
            print(f"Error fetching data: {str(e)}")
            raise

    def get_all_data(self) -> Dict:
        """
        Get all establishment data in a single response.
        
        Returns:
            Dict: Complete establishment data including nearby places, competitors, and location details
        """
        return {
            'message': 'Business information retrieved successfully',
            'submitted_data': {
                'latitude': self.latitude,
                'longitude': self.longitude,
                'business_type': self.business_type,
                'description': self.description,
                'address': {
                    'barangay': self.address.barangay,
                    'municipality': self.address.municipality,
                    'province': self.address.province,
                    'region': self.address.region
                }
            },
            'nearby_establishments': self.nearby_establishments,
            'other_establishments':self.other_establishments,
            'competitors': self.competitors,
            'best_types_summary': self.best_types_summary[1],  # Return just the counts dictionary
            'location_details': self.location_details,
            'total_establishments': len(self.nearby_establishments),
            'total_competitors': len(self.competitors)
        }

    def __str__(self) -> str:
        return f"Latitude: {self.latitude}, Longitude: {self.longitude}, Business Type: {self.business_type}, Description: {self.description}"

    def find_competitors(self) -> None:
        """Find and store competitors based on business type."""
        temp_competitors = []
        temp_other_establishments = []
        for est in self.nearby_establishments:
            if est and self.business_type in est.get('all_types',[]):
                temp_competitors.append(est)
            else:
                temp_other_establishments.append(est)

        self.competitors = temp_competitors
        self.other_establishments = temp_other_establishments

    def get_best_types_summary(self) -> Tuple[List[Dict], Dict]:
        """
        Get summary of best types of establishments in the area.
        
        Returns:
            Tuple[List[Dict], Dict]: List of establishments with best types and count dictionary
        """
        temp_best_types = [
            {**est, "all_types": self._get_best_type(est.get("all_types", []))}
            for est in self.nearby_establishments
            if est
        ]
        best_types = [est["all_types"] for est in temp_best_types]
        counts = Counter(best_types)
        return temp_best_types, dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

    @staticmethod
    def _get_best_type(types: List[str], priority_list: List[str] = TYPE_PRIORITY) -> str:
        """
        Get the best type from a list of types based on priority.
        
        Args:
            types (List[str]): List of types to check
            priority_list (List[str]): List of types in priority order
            
        Returns:
            str: Best type found or first type if none match priority
        """
        for t in priority_list:
            if t in types:
                return t
        return types[0] if types else 'unknown'

    def _parse_place_data(self, raw_place: Dict) -> Optional[Dict]:
        """
        Parse raw place data from Google Maps API.

        Now includes photo_reference and icon so the frontend can show an image and icon.
        """
        try:
            # get first photo reference if available
            photo_ref = None
            photo_width = None
            photo_height = None
            photos = raw_place.get("photos")
            if photos and isinstance(photos, list) and len(photos) > 0:
                first = photos[0]
                photo_ref = first.get("photo_reference")
                photo_width = first.get("width")
                photo_height = first.get("height")

            return {
                "name": raw_place.get("name"),
                "lat": raw_place["geometry"]["location"]["lat"],
                "lng": raw_place["geometry"]["location"]["lng"],
                "all_types": raw_place.get("types", []),
                "business_status": raw_place.get("business_status"),
                "vicinity": raw_place.get("vicinity"),
                "rating": raw_place.get("rating"),
                "user_ratings_total": raw_place.get("user_ratings_total", 0),
                "place_id": raw_place.get("place_id"),
                "photo_reference": photo_ref,
                "photo_width": photo_width,
                "photo_height": photo_height,
                "icon": raw_place.get("icon")
            }
        except KeyError as e:
            print(f"Missing key in raw place data: {e}")
            return None
    def get_location_details(self) -> Dict:
        """
        Get detailed location information including municipality.
        
        Returns:
            Dict: Location details including municipality, province, and region
        """
        try:
            address_components = self.maps_service.get_address_components(self.latitude, self.longitude)
            return {
                'message': 'Location details retrieved successfully',
                'location_details': address_components
            }
        except Exception as e:
            return {
                'error': str(e),
                'message': 'Failed to retrieve location details'
            }


# 14.282953, 120.867741