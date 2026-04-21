import django
import razorpay
import json
import hmac
import hashlib
from django.contrib.auth.models import User
from store.models import Address, Cart, Category, Order, Product, Payment
from django.shortcuts import redirect, render, get_object_or_404
from .forms import RegistrationForm, AddressForm
from django.contrib import messages
from django.views import View
import decimal
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator # for Class Based Views
from django.contrib.auth import logout as auth_logout
from django.conf import settings
from django.http import JsonResponse


# Create your views here.

def home(request):
    categories = Category.objects.filter(is_active=True, is_featured=True)[:3]
    products = Product.objects.filter(is_active=True, is_featured=True)[:8]
    context = {
        'categories': categories,
        'products': products,
    }
    return render(request, 'store/index.html', context)


def detail(request, slug):
    product = get_object_or_404(Product, slug=slug)
    related_products = Product.objects.exclude(id=product.id).filter(is_active=True, category=product.category)
    context = {
        'product': product,
        'related_products': related_products,

    }
    return render(request, 'store/detail.html', context)


def all_categories(request):
    categories = Category.objects.filter(is_active=True)
    return render(request, 'store/categories.html', {'categories':categories})


def category_products(request, slug):
    category = get_object_or_404(Category, slug=slug)
    products = Product.objects.filter(is_active=True, category=category)
    categories = Category.objects.filter(is_active=True)
    context = {
        'category': category,
        'products': products,
        'categories': categories,
    }
    return render(request, 'store/category_products.html', context)


# Authentication Starts Here

class RegistrationView(View):
    def get(self, request):
        form = RegistrationForm()
        return render(request, 'account/register.html', {'form': form})
    
    def post(self, request):
        form = RegistrationForm(request.POST)
        if form.is_valid():
            messages.success(request, "Congratulations! Registration Successful!")
            form.save()
        return render(request, 'account/register.html', {'form': form})
        

@login_required
def profile(request):
    addresses = Address.objects.filter(user=request.user)
    orders = Order.objects.filter(user=request.user)
    return render(request, 'account/profile.html', {'addresses':addresses, 'orders':orders})


@method_decorator(login_required, name='dispatch')
class AddressView(View):
    def get(self, request):
        form = AddressForm()
        return render(request, 'account/add_address.html', {'form': form})

    def post(self, request):
        form = AddressForm(request.POST)
        if form.is_valid():
            user=request.user
            locality = form.cleaned_data['locality']
            city = form.cleaned_data['city']
            state = form.cleaned_data['state']
            reg = Address(user=user, locality=locality, city=city, state=state)
            reg.save()
            messages.success(request, "New Address Added Successfully.")
        return redirect('store:profile')


@login_required
def remove_address(request, id):
    a = get_object_or_404(Address, user=request.user, id=id)
    a.delete()
    messages.success(request, "Address removed.")
    return redirect('store:profile')


@method_decorator(login_required, name='dispatch')
class LogoutView(View):
    """
    Custom logout view that displays a confirmation page
    GET: Shows logout confirmation page
    POST: Logs out the user and redirects to logout success page
    """
    def get(self, request):
        return render(request, 'account/logout.html')
    
    def post(self, request):
        auth_logout(request)
        messages.success(request, "You have been successfully logged out!")
        return render(request, 'account/logout_success.html')


@login_required
def add_to_cart(request):
    user = request.user
    product_id = request.GET.get('prod_id')
    product = get_object_or_404(Product, id=product_id)

    # Check whether the Product is alread in Cart or Not
    item_already_in_cart = Cart.objects.filter(product=product_id, user=user)
    if item_already_in_cart:
        cp = get_object_or_404(Cart, product=product_id, user=user)
        cp.quantity += 1
        cp.save()
    else:
        Cart(user=user, product=product).save()
    
    return redirect('store:cart')


@login_required
def cart(request):
    user = request.user
    cart_products = Cart.objects.filter(user=user)

    # Display Total on Cart Page
    amount = decimal.Decimal(0)
    shipping_amount = decimal.Decimal(10)
    # using list comprehension to calculate total amount based on quantity and shipping
    cp = [p for p in Cart.objects.all() if p.user==user]
    if cp:
        for p in cp:
            temp_amount = (p.quantity * p.product.price)
            amount += temp_amount

    # Customer Addresses
    addresses = Address.objects.filter(user=user)

    context = {
        'cart_products': cart_products,
        'amount': amount,
        'shipping_amount': shipping_amount,
        'total_amount': amount + shipping_amount,
        'addresses': addresses,
    }
    return render(request, 'store/cart.html', context)


@login_required
def remove_cart(request, cart_id):
    if request.method == 'GET':
        c = get_object_or_404(Cart, id=cart_id)
        c.delete()
        messages.success(request, "Product removed from Cart.")
    return redirect('store:cart')


@login_required
def plus_cart(request, cart_id):
    if request.method == 'GET':
        cp = get_object_or_404(Cart, id=cart_id)
        cp.quantity += 1
        cp.save()
    return redirect('store:cart')


@login_required
def minus_cart(request, cart_id):
    if request.method == 'GET':
        cp = get_object_or_404(Cart, id=cart_id)
        # Remove the Product if the quantity is already 1
        if cp.quantity == 1:
            cp.delete()
        else:
            cp.quantity -= 1
            cp.save()
    return redirect('store:cart')


@login_required
def checkout(request):
    """Redirect to payment page with selected address"""
    user = request.user
    address_id = request.GET.get('address')
    payment_method = request.GET.get('payment', 'online')
    
    try:
        address = get_object_or_404(Address, id=address_id, user=user)
    except:
        messages.error(request, "Please select a valid address.")
        return redirect('store:cart')
    
    # Get cart items
    cart_items = Cart.objects.filter(user=user)
    if not cart_items.exists():
        messages.error(request, "Your cart is empty.")
        return redirect('store:cart')
    
    # Calculate total amount
    total_amount = decimal.Decimal(0)
    shipping_amount = decimal.Decimal(10)
    for item in cart_items:
        total_amount += (item.quantity * item.product.price)
    
    total_amount += shipping_amount
    
    # Store in session for payment processing
    request.session['cart_items'] = list(cart_items.values_list('id', flat=True))
    request.session['address_id'] = address_id
    request.session['total_amount'] = str(total_amount)
    
    if payment_method == 'cod':
        # Cash on Delivery - Create orders directly
        return process_cod_order(request, cart_items, address)
    else:
        # Online Payment - Redirect to payment page
        return redirect('store:payment')


def process_cod_order(request, cart_items, address):
    """Process Cash on Delivery order"""
    user = request.user
    try:
        for cart_item in cart_items:
            Order(
                user=user,
                address=address,
                product=cart_item.product,
                quantity=cart_item.quantity,
                payment_status=False
            ).save()
            cart_item.delete()
        
        messages.success(request, "Order placed successfully! You will pay on delivery.")
        return redirect('store:orders')
    except Exception as e:
        messages.error(request, f"Error processing order: {str(e)}")
        return redirect('store:cart')


@login_required
def payment(request):
    """Display payment page with Razorpay checkout"""
    user = request.user
    
    # Get data from session
    cart_item_ids = request.session.get('cart_items', [])
    address_id = request.session.get('address_id')
    total_amount = decimal.Decimal(request.session.get('total_amount', 0))
    
    if not cart_item_ids or not address_id:
        messages.error(request, "Session expired. Please try again.")
        return redirect('store:cart')
    
    try:
        address = Address.objects.get(id=address_id, user=user)
        cart_items = Cart.objects.filter(id__in=cart_item_ids, user=user)
        
        if not cart_items.exists():
            messages.error(request, "Cart items not found.")
            return redirect('store:cart')
        
        # Initialize Razorpay client
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_SECRET_KEY))
        
        # Create Razorpay order
        razorpay_order = client.order.create(dict(
            amount=int(total_amount * 100),  # Amount in paise
            currency=settings.RAZORPAY_CURRENCY,
            payment_capture='0'
        ))
        
        # Store order details in session
        request.session['razorpay_order_id'] = razorpay_order['id']
        
        context = {
            'razorpay_key': settings.RAZORPAY_KEY_ID,
            'razorpay_order_id': razorpay_order['id'],
            'amount': int(total_amount * 100),
            'user_name': user.get_full_name() or user.username,
            'user_email': user.email,
            'user_phone': user.username,
            'currency': settings.RAZORPAY_CURRENCY,
            'cart_items': cart_items,
            'address': address,
            'total_amount': total_amount,
        }
        
        return render(request, 'store/payment.html', context)
    
    except Address.DoesNotExist:
        messages.error(request, "Address not found.")
        return redirect('store:cart')
    except Exception as e:
        messages.error(request, f"Error initiating payment: {str(e)}")
        return redirect('store:cart')


@login_required
def payment_verify(request):
    """Verify Razorpay payment and create orders"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request'}, status=400)
    
    try:
        data = json.loads(request.body)
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')
        
        # Verify signature
        signature_data = f'{razorpay_order_id}|{razorpay_payment_id}'
        expected_signature = hmac.new(
            settings.RAZORPAY_SECRET_KEY.encode(),
            signature_data.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if expected_signature != razorpay_signature:
            return JsonResponse({'error': 'Payment verification failed'}, status=400)
        
        # Get cart data from session
        user = request.user
        cart_item_ids = request.session.get('cart_items', [])
        address_id = request.session.get('address_id')
        total_amount = decimal.Decimal(request.session.get('total_amount', 0))
        
        if not cart_item_ids or not address_id:
            return JsonResponse({'error': 'Session expired'}, status=400)
        
        address = Address.objects.get(id=address_id, user=user)
        cart_items = Cart.objects.filter(id__in=cart_item_ids, user=user)
        
        # Create orders and update payment status
        for cart_item in cart_items:
            order = Order(
                user=user,
                address=address,
                product=cart_item.product,
                quantity=cart_item.quantity,
                payment_status=True,
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=razorpay_signature
            )
            order.save()
            cart_item.delete()
        
        # Clear session
        if 'cart_items' in request.session:
            del request.session['cart_items']
        if 'address_id' in request.session:
            del request.session['address_id']
        if 'total_amount' in request.session:
            del request.session['total_amount']
        if 'razorpay_order_id' in request.session:
            del request.session['razorpay_order_id']
        
        return JsonResponse({'success': True, 'redirect_url': '/accounts/orders/'})
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def payment_failure(request):
    """Handle payment failure"""
    messages.error(request, "Payment failed. Please try again.")
    return redirect('store:cart')


@login_required
def orders(request):
    all_orders = Order.objects.filter(user=request.user).order_by('-ordered_date')
    return render(request, 'store/orders.html', {'orders': all_orders})


def shop(request):
    return render(request, 'store/shop.html')


def test(request):
    return render(request, 'store/test.html')